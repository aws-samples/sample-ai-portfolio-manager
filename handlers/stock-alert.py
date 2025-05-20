import boto3
import json
from datetime import datetime
from botocore.exceptions import ClientError
import logging
from typing import Dict, List, Any, Optional, Tuple
import os
from decimal import Decimal

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class Config:
    """Configuration management class"""
    RECOMMENDATION_TABLE = os.environ.get('RECOMMENDATION_TABLE', 'portfolio_recommendation')
    BIAS_TABLE = os.environ.get('BIAS_TABLE', 'portfolio_bias')
    SENDER = os.environ.get('SENDER_EMAIL')
    RECIPIENT = os.environ.get('RECIPIENT_EMAIL')
    AWS_REGION = os.environ.get('AWS_REGION', "us-east-1")
    MAX_RETRIES = 3
    BATCH_SIZE = 100

class EmailFormatter:
    """Handles email content formatting"""
    
    @staticmethod
    def format_email_content(recommendations: List[Dict], bias_data: Dict) -> Tuple[str, str]:
        """
        Format both HTML and text versions of the email
        
        Args:
            recommendations: List of stock recommendations
            bias_data: Portfolio bias information
            
        Returns:
            Tuple containing HTML and text versions of the email
        """
        html_content = EmailFormatter._format_html_content(recommendations, bias_data)
        text_content = EmailFormatter._format_text_content(recommendations, bias_data)
        return html_content, text_content

    @staticmethod
    def _format_html_content(recommendations: List[Dict], bias_data: Dict) -> str:
        """Format complete email content as HTML"""
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
                .summary-box {{ 
                    background-color: #f8f9fa;
                    border: 1px solid #dee2e6;
                    border-radius: 4px;
                    padding: 15px;
                    margin-bottom: 20px;
                }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f2f2f2; }}
                tr:hover {{ background-color: #f5f5f5; }}
                .confidence-high {{ color: #28a745; }}
                .confidence-medium {{ color: #ffc107; }}
                .confidence-low {{ color: #dc3545; }}
                .risk-high {{ color: #dc3545; }}
                .risk-moderate {{ color: #ffc107; }}
                .risk-low {{ color: #28a745; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Portfolio Analysis and Recommendations</h1>
                <p>Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                
                <div class="summary-box">
                    <h2>Portfolio Bias Analysis</h2>
                    <p><strong>Bias Score:</strong> {bias_data.get('bias_score', 'N/A')}/10</p>
                    <p><strong>Sector Concentration:</strong> {bias_data.get('sector_concentration', 'N/A')}</p>
                    <p><strong>Volatility Risk:</strong> {bias_data.get('volatility_risk', 'N/A')}</p>
                    <p><strong>Recommendation:</strong> {bias_data.get('recommendation', 'N/A')}</p>
                </div>

                <h2>Stock-Specific Recommendations</h2>
                <table>
                    <tr>
                        <th>Stock Symbol</th>
                        <th>Recommendation</th>
                        <th>Confidence Score</th>
                        <th>Reasoning</th>
                    </tr>
        """
        
        for item in recommendations:
            confidence_class = EmailFormatter._get_confidence_class(item.get('confidence_score', 0))
            html_content += f"""
                <tr>
                    <td>{item.get('stockId', 'N/A')}</td>
                    <td>{item.get('recommendation', 'N/A')}</td>
                    <td class="{confidence_class}">{item.get('confidence_score', 'N/A')}%</td>
                    <td>{item.get('reasoning', 'N/A')}</td>
                </tr>
            """
        
        html_content += """
                </table>
            </div>
        </body>
        </html>
        """
        return html_content

    @staticmethod
    def _format_text_content(recommendations: List[Dict], bias_data: Dict) -> str:
        """Format complete email content as plain text"""
        text_content = f"""Portfolio Analysis and Recommendations
Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

PORTFOLIO BIAS ANALYSIS
----------------------
Bias Score: {bias_data.get('bias_score', 'N/A')}/10
Sector Concentration: {bias_data.get('sector_concentration', 'N/A')}
Volatility Risk: {bias_data.get('volatility_risk', 'N/A')}
Recommendation: {bias_data.get('recommendation', 'N/A')}

STOCK-SPECIFIC RECOMMENDATIONS
----------------------------
"""
        
        for item in recommendations:
            text_content += f"""
Stock Symbol: {item.get('stockId', 'N/A')}
Recommendation: {item.get('recommendation', 'N/A')}
Confidence Score: {item.get('confidence_score', 'N/A')}%
Reasoning: {item.get('reasoning', 'N/A')}
{'-' * 80}
"""
        return text_content

    @staticmethod
    def _get_confidence_class(score: float) -> str:
        """Determine confidence class based on score"""
        if score >= 70:
            return "confidence-high"
        elif score >= 40:
            return "confidence-medium"
        return "confidence-low"

class DynamoDBHandler:
    """Handles DynamoDB operations"""
    
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb')
        self.recommendation_table = self.dynamodb.Table(Config.RECOMMENDATION_TABLE)
        self.bias_table = self.dynamodb.Table(Config.BIAS_TABLE)

    def get_recommendations(self) -> List[Dict]:
        """Fetch recommendations from DynamoDB"""
        try:
            response = self.recommendation_table.scan()
            return response.get('Items', [])
        except ClientError as e:
            logger.error(f"Error fetching recommendations: {str(e)}")
            raise

    def get_bias_data(self, user_id: str) -> Dict:
        """Fetch bias data from DynamoDB"""
        try:
            response = self.bias_table.get_item(
                Key={'userId': user_id}
            )
            return response.get('Item', {})
        except ClientError as e:
            logger.error(f"Error fetching bias data: {str(e)}")
            raise

class EmailSender:
    """Handles email sending operations"""
    
    def __init__(self):
        self.ses_client = boto3.client('ses', region_name=Config.AWS_REGION)

    def send_email(self, html_content: str, text_content: str) -> None:
        """Send email using Amazon SES"""
        try:
            response = self.ses_client.send_email(
                Source=Config.SENDER,
                Destination={'ToAddresses': [Config.RECIPIENT]},
                Message={
                    'Subject': {
                        'Data': 'Your Portfolio Analysis and Recommendations'
                    },
                    'Body': {
                        'Text': {
                            'Data': text_content
                        },
                        'Html': {
                            'Data': html_content
                        }
                    }
                }
            )
            logger.info(f"Email sent! Message ID: {response['MessageId']}")
        except ClientError as e:
            logger.error(f"Error sending email: {str(e)}")
            raise

def lambda_handler(event, context):
    """AWS Lambda handler"""
    try:
        # Initialize handlers
        dynamodb_handler = DynamoDBHandler()
        email_sender = EmailSender()

        # Fetch data
        recommendations = dynamodb_handler.get_recommendations()
        
        # Get userId from risk profile table
        risk_profile_table = dynamodb_handler.dynamodb.Table('portfolioprofile')
        response = risk_profile_table.scan(Limit=1)
        user_id = response.get('Items', [{}])[0].get('userId', 'user-default')
        
        bias_data = dynamodb_handler.get_bias_data(user_id)

        # Format email content
        html_content, text_content = EmailFormatter.format_email_content(
            recommendations, bias_data
        )

        # Send email
        email_sender.send_email(html_content, text_content)

        return {
            'statusCode': 200,
            'body': json.dumps('Email sent successfully!')
        }

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
