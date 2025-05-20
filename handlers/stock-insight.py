import json
import logging
# Use a specific version of yfinance that's compatible with Python 3.9
# The newer versions use the pipe operator (|) for type hints which requires Python 3.10+
import yfinance as yfinance
import boto3
import time
from decimal import Decimal
import numpy as np
import pandas as pd
import datetime
from botocore.exceptions import ClientError
from typing import Dict, Any, List

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS resources with direct table names
try:
    dynamodb = boto3.resource('dynamodb')
    portfolio_stock_trend = dynamodb.Table('portfolio_stock_trend')
    portfolio_stock_fundamentals = dynamodb.Table('portfolio_stock_fundamentals')
except Exception as e:
    logger.error(f"Failed to initialize AWS resources: {str(e)}")
    raise

class DynamoDBHandler:
    def __init__(self):
        self.dynamodb = boto3.client('dynamodb')
        self.dynamodb_resource = boto3.resource('dynamodb')
        self.earnings_table = self.dynamodb_resource.Table('portfolio-stock-earnings')

    def get_primary_key_values(self, table_name: str) -> Dict[str, Any]:
        """
        Retrieve primary key values from a DynamoDB table
        """
        try:
            table = self.dynamodb_resource.Table(table_name)
            response = self.dynamodb.describe_table(TableName=table_name)
            key_schema = response['Table']['KeySchema']
            primary_keys = [k['AttributeName'] for k in key_schema]
            
            key_values = []
            last_evaluated_key = None
            
            while True:
                scan_kwargs = {
                    'ProjectionExpression': ','.join(primary_keys)
                }
                if last_evaluated_key:
                    scan_kwargs['ExclusiveStartKey'] = last_evaluated_key
                
                response = table.scan(**scan_kwargs)
                
                for item in response['Items']:
                    if len(primary_keys) == 1:
                        key_values.append(item[primary_keys[0]])
                    else:
                        key_values.extend([item[pk] for pk in primary_keys])
                
                last_evaluated_key = response.get('LastEvaluatedKey')
                if not last_evaluated_key:
                    break
            
            return {
                'success': True,
                'key_values': list(set(key_values)),  # Remove duplicates
                'count': len(key_values)
            }
            
        except ClientError as e:
            logger.error(f"DynamoDB error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            raise

class DecimalEncoder:
    @staticmethod
    def convert_to_decimal(obj: Any) -> Any:
        """Convert numeric types to Decimal for DynamoDB compatibility"""
        try:
            if isinstance(obj, (float, np.float64, int, np.int64)):
                return Decimal(str(obj))
            elif isinstance(obj, dict):
                return {k: DecimalEncoder.convert_to_decimal(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [DecimalEncoder.convert_to_decimal(item) for item in obj]
            return obj
        except Exception as e:
            logger.error(f"Error converting to Decimal: {str(e)}")
            return None

class StockAnalyzer:
    def __init__(self, stock_id: str):
        self.stock_id = stock_id
        self.stock = yfinance.Ticker(stock_id)
        
    def get_technical_indicators(self) -> Dict[str, Any]:
        """Calculate technical indicators for the stock"""
        try:
            history = self.stock.history(period="6mo")
            if history.empty:
                raise ValueError(f"No historical data available for {self.stock_id}")

            # Technical Data
            last_close = history["Close"].iloc[-1]
            moving_avg_50 = history["Close"].rolling(window=50).mean().iloc[-1]
            
            # RSI Calculation
            delta = history["Close"].diff()
            gain = np.where(delta > 0, delta, 0)
            loss = np.where(delta < 0, -delta, 0)
            avg_gain = pd.Series(gain).rolling(window=14).mean()
            avg_loss = pd.Series(loss).rolling(window=14).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            # MACD Calculation
            short_ema = history["Close"].ewm(span=12, adjust=False).mean()
            long_ema = history["Close"].ewm(span=26, adjust=False).mean()
            macd = short_ema - long_ema
            signal_line = macd.ewm(span=9, adjust=False).mean()

            return {
                "last_close": last_close,
                "moving_avg_50": moving_avg_50,
                "rsi": rsi.iloc[-1],
                "macd": macd.iloc[-1],
                "signal": signal_line.iloc[-1],
                "volume": history["Volume"].iloc[-1]
            }
        except Exception as e:
            logger.error(f"Error calculating technical indicators for {self.stock_id}: {str(e)}")
            raise
    def _update_trend_table(self, technical_data: Dict[str, Any], info: Dict[str, Any]) -> None:
        """
        Update the trend table with technical indicators and market data
        
        Args:
            technical_data: Dictionary containing technical indicators
            info: Dictionary containing stock information
        """
        try:
            update_expression = '''SET 
                last_close_price = :l, 
                moving_avg_50 = :m,
                rsi = :r,
                macd = :macd,
                macd_signal = :s,
                volume = :v,
                market_cap = :mcap,
                pe_ratio = :pe,
                dividend_yield = :dividend,
                #ts = :t'''

            expression_values = {
                ':l': DecimalEncoder.convert_to_decimal(technical_data["last_close"]),
                ':m': DecimalEncoder.convert_to_decimal(technical_data["moving_avg_50"]),
                ':r': DecimalEncoder.convert_to_decimal(technical_data["rsi"]),
                ':macd': DecimalEncoder.convert_to_decimal(technical_data["macd"]),
                ':s': DecimalEncoder.convert_to_decimal(technical_data["signal"]),
                ':v': DecimalEncoder.convert_to_decimal(technical_data["volume"]),
                ':mcap': DecimalEncoder.convert_to_decimal(info.get("marketCap", 0)),
                ':pe': DecimalEncoder.convert_to_decimal(info.get("trailingPE", 0)),
                ':dividend': DecimalEncoder.convert_to_decimal(info.get("dividendYield", 0) * 100),
                ':t': DecimalEncoder.convert_to_decimal(datetime.datetime.now().isoformat())
            }

            # Validate data before updating
            if any(v is None for v in expression_values.values()):
                raise ValueError("One or more required values are None")

            response = portfolio_stock_trend.update_item(
                Key={'stockId': self.stock_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames={'#ts': 'timestamp'},
                ReturnValues='ALL_NEW'  # Returns the new values of the updated item
            )
            
            logger.info(f"Successfully updated trend data for {self.stock_id}")
            logger.debug(f"Update response: {response}")
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"DynamoDB error updating trend table - Code: {error_code}, Message: {error_message}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error updating trend table: {str(e)}")
            raise

    def _update_fundamentals_table(self, info: Dict[str, Any]) -> None:
        """
        Update the fundamentals table with stock fundamental data
        
        Args:
            info: Dictionary containing stock information
        """
        try:
            update_expression = '''SET 
                Industry = :i,
                market_cap = :mcap,
                peratio = :pe,
                eps = :eps,
                dividend_yield = :dividend,
                fifty_two_week_high = :high,
                fifty_two_week_low = :low,
                fifty_day_ma = :ma50,
                two_hundred_day_ma = :ma200,
                debttoequity = :debttoequity,
                #ts = :t'''

            expression_values = {
                ':i': info.get("industry", "N/A"),
                ':mcap': DecimalEncoder.convert_to_decimal(info.get("marketCap", 0)),
                ':pe': DecimalEncoder.convert_to_decimal(info.get("trailingPE", 0)),
                ':eps': DecimalEncoder.convert_to_decimal(info.get("trailingEps", 0)),
                ':dividend': DecimalEncoder.convert_to_decimal(info.get("dividendYield", 0) * 100),
                ':high': DecimalEncoder.convert_to_decimal(info.get("fiftyTwoWeekHigh", 0)),
                ':low': DecimalEncoder.convert_to_decimal(info.get("fiftyTwoWeekLow", 0)),
                ':ma50': DecimalEncoder.convert_to_decimal(info.get("fiftyDayAverage", 0)),
                ':ma200': DecimalEncoder.convert_to_decimal(info.get("twoHundredDayAverage", 0)),
                ':debttoequity': DecimalEncoder.convert_to_decimal(info.get("debtToEquity", 0)),
                ':t': DecimalEncoder.convert_to_decimal(datetime.datetime.now().isoformat())
            }

            # Add conditional update to prevent overwriting with invalid data
            condition_expression = 'attribute_exists(stockId)'

            response = portfolio_stock_fundamentals.update_item(
                Key={'stockId': self.stock_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames={'#ts': 'timestamp'},
                ReturnValues='ALL_NEW'
            )

            logger.info(f"Successfully updated fundamental data for {self.stock_id}")
            logger.debug(f"Update response: {response}")

        except ClientError as e:
                error_code = e.response['Error']['Code']
                error_message = e.response['Error']['Message']
                logger.error(f"DynamoDB error updating fundamentals table - Code: {error_code}, Message: {error_message}")
                raise
        except Exception as e:
            logger.error(f"Unexpected error updating fundamentals table: {str(e)}")
            raise

    
    def update_stock_data(self):
        """Update stock data in DynamoDB tables"""
        try:
            technical_data = self.get_technical_indicators()
            info = self.stock.info

            # Update trend table
            self._update_trend_table(technical_data, info)
            
            # Update fundamentals table
            self._update_fundamentals_table(info)
            return {
                "success": True,
                "message": "Data updated successfully"
            }


        except Exception as e:
            logger.error(f"Error updating stock data for {self.stock_id}: {str(e)}")
            raise

    

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, str]:
    """AWS Lambda handler function"""
    try:

        if 'detail-type' in event:
            logger.info("Processing scheduled event")
            # Process all stocks for scheduled updates
            dynamo_handler = DynamoDBHandler()
            portfolio_response = dynamo_handler.get_primary_key_values("portfolio")
            if not portfolio_response['success']:
                raise ValueError("Failed to retrieve portfolio data")
            
            tickers = portfolio_response['key_values']
            logger.info(f"Processing {len(tickers)} unique tickers")
        
            # Fetch earnings data for each ticker
            results = []
            for ticker in tickers:
                analyzer = StockAnalyzer(ticker)
                result = analyzer.update_stock_data()
                results.append(result)
            
        # Count successes and failures
            successes = sum(1 for r in results if r['success'])
            failures = sum(1 for r in results if not r['success'])
        
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Processing complete',
                    'total_processed': len(results),
                    'successful': successes,
                    'failed': failures
                })
            }

        elif 'Records' in event:        
            for record in event['Records']:
                if record["eventName"] == "INSERT":
                    stock_id = record["dynamodb"]["NewImage"]["stockId"]["S"]
                    logger.info(f"Processing stock: {stock_id}")
                
                    analyzer = StockAnalyzer(stock_id)
                    analyzer.update_stock_data()
                
        return {
            'statusCode': 200,
            'body': json.dumps('Stock data updated successfully')
        }
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error processing stock data: {str(e)}')
        }