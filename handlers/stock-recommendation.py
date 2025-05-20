import json
import time
import os
from datetime import datetime
from decimal import Decimal
import logging
from typing import Dict, List, Any, Optional
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from functools import wraps

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
CONFIG = {
    'stock_ids_table': 'portfolio',
    'risk_profile': 'portfolioprofile',
    'fundamentals_table': 'portfolio_stock_fundamentals',
    'technicals_table': 'portfolio_stock_trend',
    'earnings_table': 'portfolio_earnings',
    'recommendations_table': 'portfolio_recommendation',
    'portfolio_bias': 'portfolio_bias',
    'api_delay': 0.1,
    
    # Bedrock configuration
    'bedrock': {
        'model_id': os.environ.get('BEDROCK_MODEL_ID', 'amazon.nova-micro-v1:0'),
        'max_tokens': int(os.environ.get('BEDROCK_MAX_TOKENS', '1000')),
        'temperature': float(os.environ.get('BEDROCK_TEMPERATURE', '0.0')),
        'top_p': float(os.environ.get('BEDROCK_TOP_P', '0.9')),
        'prompts': {
            'stock_analysis': """
            Analyze the following stock data and provide a BUY, SELL, or HOLD recommendation:
            
            Fundamentals: {fundamentals}
            Technicals: {technicals}
            Earnings and financial metrics with holdings data: {earnings}
            RiskProfile: {riskprofile}
            
            Provide the recommendation in JSON format with:
            - recommendation (BUY/SELL/HOLD)
            - confidence_score (0-100)
            - reasoning (brief explanation)

            Respond with valid JSON only, without any additional text, explanations, or formatting.
            Do not include markdown formatting or code blocks.
            The response must be parseable by json.loads().
            """,
            'portfolio_bias': """
            Analyze the following stock portfolio and detect any biases:
            {portfolio_data}
            
            Evaluate:
            - Sector concentration risk
            - Portfolio volatility
            - Market capitalization balance (Small, Mid, Large-cap)
            - Diversification across industries
            - Recommendations to optimize for lower risk and better diversification.
            Provide a summary with bias rating from 1 (well-balanced) to 10 (highly biased)
            Provide the analysis in JSON format with:
            - bias_score
            - volatility_risk
            - sector_concentration
            - recommendation
            Respond with valid JSON only, without any additional text, explanations, or formatting.
            Do not include markdown formatting or code blocks.
            The response must be parseable by json.loads().
            """
        }
    }
}

class AWSServiceBase:
    def __init__(self):
        self._connections = {}
        self.MAX_RETRIES = 4
        self.BASE_DELAY = 0.75

    def get_service(self, service_name: str):
        if service_name not in self._connections:
            self._connections[service_name] = boto3.client(service_name)
        return self._connections[service_name]

    def retry_with_backoff(self, retryable_errors=None):
        if retryable_errors is None:
            retryable_errors = ['ProvisionedThroughputExceededException', 'ThrottlingException']

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                retry_count = 0
                while retry_count < self.MAX_RETRIES:
                    try:
                        return func(*args, **kwargs)
                    except ClientError as e:
                        error_code = e.response['Error']['Code']
                        if error_code in retryable_errors and retry_count < self.MAX_RETRIES:
                            retry_count += 1
                            wait_time = self.BASE_DELAY * (2 ** retry_count)
                            logger.warning(f"Retrying after {wait_time}s. Error: {error_code}")
                            time.sleep(wait_time)
                            continue
                        raise
                raise Exception(f"Max retries ({self.MAX_RETRIES}) exceeded")
            return wrapper
        return decorator

class DataValidationMixin:
    @staticmethod
    def validate_required_fields(data: Dict, required_fields: List[str]) -> bool:
        return all(field in data and data[field] is not None for field in required_fields)

    @staticmethod
    def sanitize_data(data: Dict) -> Dict:
        sanitized = {k: v for k, v in data.items() if v is not None and v != ""}
        for key, value in sanitized.items():
            if isinstance(value, str) and value.replace('.', '').isdigit():
                sanitized[key] = Decimal(value)
        return sanitized

    @staticmethod
    def convert_decimals(obj):
        """Convert numeric types to Decimal for DynamoDB compatibility"""
        try:
            if isinstance(obj, dict):
                return {k: BedrockAnalyzer.convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [BedrockAnalyzer.convert_decimals(x) for x in obj]
            elif isinstance(obj, Decimal):
                return float(obj)
            return obj
        except Exception as e:
            logger.error(f"Error converting to Decimal: {str(e)}")
            return None        

class DynamoDBManager(AWSServiceBase, DataValidationMixin):
    def __init__(self):
        super().__init__()
        self.dynamodb = boto3.resource('dynamodb')
        self.BATCH_SIZE = 100
        self._risk_profile = None

    @property
    def risk_profile(self) -> Optional[Dict]:
        if self._risk_profile is None:
            self._risk_profile = self._fetch_risk_profile()
        return self._risk_profile

    def _fetch_risk_profile(self) -> Optional[Dict]:
        table = self.dynamodb.Table(CONFIG['risk_profile'])
        response = table.scan(Limit=1, ConsistentRead=True)
        items = response.get('Items', [])
        return items[0] if items else None

    def get_stock_ids(self, table_name: str) -> List[str]:
        table = self.dynamodb.Table(table_name)
        stock_ids = []
        last_evaluated_key = None

        while True:
            scan_params = {
                'ProjectionExpression': 'stockId',
                'ConsistentRead': True
            }
            if last_evaluated_key:
                scan_params['ExclusiveStartKey'] = last_evaluated_key

            response = table.scan(**scan_params)
            stock_ids.extend([item['stockId'] for item in response.get('Items', [])])
            
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break

        return list(set(stock_ids))

    def get_stock_data(self, table_name: str, stock_id: str) -> Optional[Dict]:
        table = self.dynamodb.Table(table_name)
        response = table.query(
            KeyConditionExpression=Key('stockId').eq(stock_id),
            ConsistentRead=True
        )
        items = response.get('Items', [])
        return self.sanitize_data(items[0]) if items else None

    def batch_get_stock_data(self, table_name: str, stock_ids: List[str]) -> Dict[str, Dict]:
        results = {}
        for i in range(0, len(stock_ids), self.BATCH_SIZE):
            batch = stock_ids[i:i + self.BATCH_SIZE]
            response = self.dynamodb.batch_get_item(
                RequestItems={
                    table_name: {
                        'Keys': [{'stockId': stock_id} for stock_id in batch],
                        'ConsistentRead': True
                    }
                }
            )
            for item in response['Responses'].get(table_name, []):
                results[item['stockId']] = self.sanitize_data(item)
        return results

class BedrockManager(AWSServiceBase):
    def __init__(self):
        super().__init__()
        self.bedrock = self.get_service('bedrock-runtime')
        self.MAX_TOKENS = 2000
        self.CHUNK_SIZE = 1500  # Safe size for chunking data

    def chunk_data(self, data: Dict) -> List[Dict]:
        """Split large data structures into smaller chunks"""
        chunks = []
        current_chunk = {}
        current_size = 0
        
        for key, value in data.items():
            value_str = json.dumps(value)
            if len(value_str) > self.CHUNK_SIZE:
                # If single value is too large, truncate it
                value_str = value_str[:self.CHUNK_SIZE] + "..."
                value = json.loads(value_str)
            
            if current_size + len(value_str) > self.CHUNK_SIZE:
                chunks.append(current_chunk)
                current_chunk = {}
                current_size = 0
            
            current_chunk[key] = value
            current_size += len(value_str)
        
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    @property
    def retryable_bedrock_errors(self):
        return [
            'ThrottlingException',
            'ModelTimeoutException', 
            'ServiceQuotaExceededException'
        ]
    def get_inference(self, prompt: str, model_id: str = None) -> Dict:
        # Use model_id from config if not provided
        model_id = model_id or CONFIG['bedrock']['model_id']
        max_tokens = CONFIG['bedrock']['max_tokens']
        temperature = CONFIG['bedrock']['temperature']
        top_p = CONFIG['bedrock']['top_p']
        
        retry_count = 0
        while retry_count < self.MAX_RETRIES:        
            try:
                body = json.dumps({
                    "inferenceConfig": {
                        "max_new_tokens": min(max_tokens, self.MAX_TOKENS),
                        "temperature": temperature,
                        "top_p": top_p,
                    },
                    "messages": [{"role": "user", "content": [{"text": prompt}]}]
                })

                response = self.bedrock.invoke_model(
                    body=body,
                    modelId=model_id,
                    contentType="application/json",
                    accept="application/json"
                )
                response_body = json.loads(response.get('body').read())
                return json.loads(response_body['output']['message']['content'][0]['text'])
            
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == 'ValidationException' and 'token limit exceeded' in str(e):
                    logger.warning("Token limit exceeded, attempting to reduce prompt size")
                    # Truncate the prompt and retry
                    truncated_prompt = prompt[:int(len(prompt)*0.7)]  # Reduce by 30%
                    return self.get_inference(truncated_prompt, model_id)
                if error_code in self.retryable_bedrock_errors and retry_count < self.MAX_RETRIES:
                    retry_count += 1
                    wait_time = self.BASE_DELAY * (2 ** retry_count)
                    logger.warning(f"Retrying after {wait_time}s. Error: {error_code}")
                    time.sleep(wait_time)
                    continue                
                raise
            except Exception as e:
                logger.error(f"Unexpected error in get_inference: {str(e)}")
                raise   
        raise Exception(f"Max retries ({self.MAX_RETRIES}) exceeded")             

    def build_analysis_prompt(self, stock_data: Dict) -> str:
        # Use the template from config and format it with the stock data
        template = CONFIG['bedrock']['prompts']['stock_analysis']
        return template.format(
            fundamentals=json.dumps(stock_data['fundamentals'], indent=2),
            technicals=json.dumps(stock_data['technicals'], indent=2),
            earnings=json.dumps(stock_data['earnings'], indent=2),
            riskprofile=json.dumps(stock_data['riskprofile'], indent=2)
        )

    def analyze_portfolio_bias_prompt(self, portfolio_data: Dict) -> str:
        # Use the template from config and format it with the portfolio data
        template = CONFIG['bedrock']['prompts']['portfolio_bias']
        return template.format(
            portfolio_data=json.dumps(portfolio_data, indent=2)
        )

class RecommendationManager(AWSServiceBase, DataValidationMixin):
    def __init__(self):
        super().__init__()
        self.dynamodb = boto3.resource('dynamodb')

    def store_recommendation(self, table_name: str, stock_id: str, recommendation: Dict) -> bool:
        if not self.validate_required_fields(recommendation, 
                                          ['recommendation', 'confidence_score', 'reasoning']):
            raise ValueError("Invalid recommendation data")

        table = self.dynamodb.Table(table_name)
        table.update_item(
            Key={'stockId': stock_id},
            UpdateExpression='SET recommendation = :r, confidence_score = :c, '
                           'reasoning = :s, #ts = :t',
            ExpressionAttributeValues={
                ':r': recommendation['recommendation'],
                ':c': Decimal(str(recommendation['confidence_score'])),
                ':s': recommendation['reasoning'],
                ':t': datetime.now().isoformat()
            },
            ExpressionAttributeNames={'#ts': 'timestamp'}
        )
        return True

    def store_bias_details(self, table_name: str, bias: Dict) -> bool:
        if not self.validate_required_fields(bias, 
                                          ['bias_score', 'volatility_risk', 
                                           'sector_concentration', 'recommendation']):
            raise ValueError("Invalid bias data")

        # Get the userId from the risk profile table
        risk_profile_table = self.dynamodb.Table(CONFIG['risk_profile'])
        response = risk_profile_table.scan(Limit=1, ConsistentRead=True)
        items = response.get('Items', [])
        user_id = items[0]['userId'] if items else "user-default"

        table = self.dynamodb.Table(table_name)
        table.update_item(
            Key={'userId': user_id},
            UpdateExpression='SET bias_score = :b, volatility_risk = :v, '
                           'sector_concentration = :s, recommendation = :r, #ts = :t',
            ExpressionAttributeValues={
                ':b': bias['bias_score'],
                ':v': bias['volatility_risk'],
                ':s': bias['sector_concentration'],
                ':r': bias['recommendation'],
                ':t': datetime.now().isoformat()
            },
            ExpressionAttributeNames={'#ts': 'timestamp'}
        )
        return True

class StockAnalysisOrchestrator:
    def __init__(self, config: Dict[str, str]):
        self.config = config
        self.dynamo_manager = DynamoDBManager()
        self.bedrock_manager = BedrockManager()
        self.recommendation_manager = RecommendationManager()

    def _gather_stock_data(self, stock_id: str) -> Optional[Dict]:
        fundamentals = self.dynamo_manager.get_stock_data(
            self.config['fundamentals_table'], stock_id)
        technicals = self.dynamo_manager.get_stock_data(
            self.config['technicals_table'], stock_id)
        earnings = self.dynamo_manager.get_stock_data(
            self.config['earnings_table'], stock_id)
        
        if not all([fundamentals, technicals, earnings]):
            return None

        return {
            'fundamentals': fundamentals,
            'technicals': technicals,
            'earnings': earnings,
            'riskprofile': self.dynamo_manager.risk_profile
        }
    @staticmethod
    def convert_decimals(obj):
        """Convert numeric types to Decimal for DynamoDB compatibility"""
        try:
            if isinstance(obj, dict):
                return {k: StockAnalysisOrchestrator.convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [StockAnalysisOrchestrator.convert_decimals(x) for x in obj]
            elif isinstance(obj, Decimal):
                return float(obj)
            return obj
        except Exception as e:
            logger.error(f"Error converting to Decimal: {str(e)}")
            return None  
    def process_stock(self, stock_id: str) -> Dict:
        try:
            stock_data = self._gather_stock_data(stock_id)
            if not stock_data:
                return {'stock_id': stock_id, 'success': False, 'error': "Missing data"}

            prompt = self.bedrock_manager.build_analysis_prompt(self.convert_decimals(stock_data))
            recommendation = self.bedrock_manager.get_inference(prompt)

            success = self.recommendation_manager.store_recommendation(
                self.config['recommendations_table'],
                stock_id,
                recommendation
            )

            return {
                'stock_id': stock_id,
                'success': success,
                'recommendation': recommendation if success else None
            }

        except Exception as e:
            logger.error(f"Error processing stock {stock_id}: {str(e)}")
            return {'stock_id': stock_id, 'success': False, 'error': str(e)}

    def process_portfolio(self, stock_ids: List[str]) -> Dict:
        try:
            portfolio_data = {
                'fundamentals': self.dynamo_manager.batch_get_stock_data(
                    self.config['fundamentals_table'], stock_ids),
                'technicals': self.dynamo_manager.batch_get_stock_data(
                    self.config['technicals_table'], stock_ids)
            }

            prompt = self.bedrock_manager.analyze_portfolio_bias_prompt(self.convert_decimals(portfolio_data))
            bias_analysis = self.bedrock_manager.get_inference(prompt)
            self.recommendation_manager.store_bias_details(
                self.config['portfolio_bias'],
                bias_analysis
            )

            return bias_analysis

        except Exception as e:
            logger.error(f"Error processing portfolio: {str(e)}")
            return {'success': False, 'error': str(e)}

def lambda_handler(event: Dict, context: Any) -> Dict:
    try:
        orchestrator = StockAnalysisOrchestrator(CONFIG)
        stock_ids = orchestrator.dynamo_manager.get_stock_ids(CONFIG['stock_ids_table'])

        # Process portfolio bias
        portfolio_analysis = orchestrator.process_portfolio(stock_ids)

        # Process individual stocks
        results = []
        successful_count = 0
        error_count = 0

        for stock_id in stock_ids:
            result = orchestrator.process_stock(stock_id)
            results.append(result)
            
            if result['success']:
                successful_count += 1
            else:
                error_count += 1

            time.sleep(CONFIG['api_delay'])

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing complete',
                'total_processed': len(stock_ids),
                'successful': successful_count,
                'failed': error_count,
                'portfolio_analysis': portfolio_analysis,
                'results': results
            }, default=str)
        }

    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Internal server error'
            })
        }