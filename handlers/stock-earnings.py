import json
import boto3
import yfinance as yf
import numpy as np
import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError
import logging
import time
import pandas as pd

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class DynamoDBHandler:
    def __init__(self):
        self.dynamodb = boto3.client('dynamodb')
        self.dynamodb_resource = boto3.resource('dynamodb')
        self.earnings_table = self.dynamodb_resource.Table('portfolio_earnings')

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
            if obj is None:
                return None
            elif isinstance(obj, (float, int)):
                return Decimal(str(obj))
            elif isinstance(obj, np.number):
                return Decimal(str(float(obj)))
            elif isinstance(obj, dict):
                return {k: DecimalEncoder.convert_to_decimal(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [DecimalEncoder.convert_to_decimal(item) for item in obj]
            elif isinstance(obj, np.ndarray):
                return [DecimalEncoder.convert_to_decimal(item) for item in obj.tolist()]
            elif hasattr(obj, 'isoformat'):  # Handle datetime objects
                return obj.isoformat()
            return obj
        except Exception as e:
            logger.error(f"Error converting to Decimal: {str(e)} for object type {type(obj)}")
            # Return a safe value instead of None to avoid further errors
            if isinstance(obj, (float, int, np.number)):
                return Decimal('0')
            return str(obj) if obj is not None else None

class EarningsDataFetcher:
    def __init__(self):
        self.earnings_table = boto3.resource('dynamodb').Table('portfolio_earnings')
        self.rate_limit_delay = 0.2  # 200ms delay between API calls to avoid rate limiting

    def fetch_earnings(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch earnings data from Yahoo Finance API
        """
        try:
            # Get stock info using yfinance
            stock = yf.Ticker(ticker)
            
            # Get income statement data (annual and quarterly)
            annual_income_stmt = stock.income_stmt
            quarterly_income_stmt = stock.quarterly_income_stmt
            
            # Get earnings history for estimates and surprises
            earnings_history = stock.earnings_history
            
            # We're not using analyst recommendations anymore
            
            # Get institutional holdings data
            major_holders = stock.major_holders
            institutional_holders = stock.institutional_holders
            mutualfund_holders = stock.mutualfund_holders
            
            # Get additional data from stock.info
            stock_info = stock.info
            
            # Format annual earnings data
            annual_earnings = []
            if annual_income_stmt is not None and not annual_income_stmt.empty:
                # Get Net Income row
                if 'Net Income' in annual_income_stmt.index:
                    net_income_row = annual_income_stmt.loc['Net Income']
                    # Get Basic EPS row if available, otherwise calculate from net income and shares outstanding
                    if 'Basic EPS' in annual_income_stmt.index:
                        eps_row = annual_income_stmt.loc['Basic EPS']
                    else:
                        # Try to get shares outstanding
                        try:
                            shares = stock.info.get('sharesOutstanding', None)
                            if shares and shares > 0:
                                eps_row = net_income_row / shares
                            else:
                                eps_row = None
                        except:
                            eps_row = None
                    
                    # Create annual earnings entries
                    for date, net_income in net_income_row.items():
                        eps = eps_row[date] if eps_row is not None and date in eps_row else None
                        if pd.notna(net_income):
                            annual_earnings.append({
                                "fiscalDateEnding": date.strftime("%Y-%m-%d"),
                                "reportedEPS": float(eps) if eps is not None and pd.notna(eps) else None,
                                "netIncome": float(net_income) if pd.notna(net_income) else None
                            })
            
            # Format quarterly earnings data
            quarterly_earnings_list = []
            if quarterly_income_stmt is not None and not quarterly_income_stmt.empty:
                # Get Net Income row
                if 'Net Income' in quarterly_income_stmt.index:
                    net_income_row = quarterly_income_stmt.loc['Net Income']
                    # Get Basic EPS row if available
                    if 'Basic EPS' in quarterly_income_stmt.index:
                        eps_row = quarterly_income_stmt.loc['Basic EPS']
                    else:
                        eps_row = None
                    
                    # Create quarterly earnings entries
                    for date, net_income in net_income_row.items():
                        eps = eps_row[date] if eps_row is not None and date in eps_row else None
                        if pd.notna(net_income):
                            # Try to find matching entry in earnings history for estimates and surprises
                            estimated_eps = None
                            surprise = None
                            surprise_pct = None
                            
                            if earnings_history is not None and not earnings_history.empty:
                                # Find closest date in earnings history
                                for _, row in earnings_history.iterrows():
                                    report_date = row.get('reportedDate', None)
                                    if report_date and abs((report_date - date).days) < 30:  # Within 30 days
                                        estimated_eps = row.get('epsEstimate', None)
                                        actual_eps = row.get('epsActual', None)
                                        if estimated_eps is not None and actual_eps is not None:
                                            surprise = actual_eps - estimated_eps
                                            if estimated_eps != 0:
                                                surprise_pct = (surprise / abs(estimated_eps)) * 100
                                        break
                            
                            quarterly_earnings_list.append({
                                "fiscalDateEnding": date.strftime("%Y-%m-%d"),
                                "reportedDate": date.strftime("%Y-%m-%d"),
                                "reportedEPS": float(eps) if eps is not None and pd.notna(eps) else None,
                                "netIncome": float(net_income) if pd.notna(net_income) else None,
                                "estimatedEPS": float(estimated_eps) if estimated_eps is not None and pd.notna(estimated_eps) else None,
                                "surprise": float(surprise) if surprise is not None and pd.notna(surprise) else None,
                                "surprisePercentage": float(surprise_pct) if surprise_pct is not None and pd.notna(surprise_pct) else None
                            })
            
            # Get calendar data for upcoming earnings
            calendar = stock.calendar
            next_earnings = None
            if calendar is not None:
                next_earnings_date = calendar.get('Earnings Date', None)
                if next_earnings_date is not None and len(next_earnings_date) > 0:
                    next_date = next_earnings_date[0]
                    if isinstance(next_date, datetime.datetime):
                        next_earnings = next_date.strftime("%Y-%m-%d")
                    else:
                        next_earnings = str(next_date)
            
            # Initialize price_targets variable
            price_targets = None
            
            # Process price targets from stock.info
            if stock_info:
                try:
                    current_price = stock_info.get('currentPrice', stock_info.get('regularMarketPrice', None))
                    target_mean_price = stock_info.get('targetMeanPrice')
                    target_high_price = stock_info.get('targetHighPrice')
                    target_low_price = stock_info.get('targetLowPrice')
                    target_median_price = stock_info.get('targetMedianPrice')
                    
                    if any([target_mean_price, target_high_price, target_low_price, target_median_price]):
                        price_targets = {
                            'low': float(target_low_price) if target_low_price is not None else None,
                            'high': float(target_high_price) if target_high_price is not None else None,
                            'mean': float(target_mean_price) if target_mean_price is not None else None,
                            'median': float(target_median_price) if target_median_price is not None else None,
                            'currentPrice': float(current_price) if current_price is not None else None,
                            'numberOfAnalysts': int(stock_info.get('numberOfAnalystOpinions')) if stock_info.get('numberOfAnalystOpinions') else None
                        }
                        
                        # Calculate upside potential
                        if price_targets['mean'] is not None and price_targets['currentPrice'] is not None and price_targets['currentPrice'] > 0:
                            price_targets['upsidePotential'] = ((price_targets['mean'] / price_targets['currentPrice']) - 1) * 100
                except Exception as e:
                    logger.warning(f"Error processing price targets for {ticker}: {str(e)}")
                    price_targets = None
            
            # Process institutional holdings
            holdings_data = {}
            
            # Major holders (percentage data)
            if major_holders is not None and not major_holders.empty:
                try:
                    # Safe way to extract data from major_holders DataFrame
                    major_holders_data = {}
                    
                    # Process each row safely
                    for i in range(min(4, len(major_holders))):
                        try:
                            # Get the value safely
                            if len(major_holders.columns) > 0:
                                value = major_holders.iloc[i, 0]
                                
                                # Process based on row index
                                if i == 0:  # Insiders percentage
                                    if isinstance(value, str) and '%' in value:
                                        major_holders_data['insidersPercentage'] = float(value.replace('%', '').strip()) / 100
                                    elif isinstance(value, (float, int, np.number)):
                                        major_holders_data['insidersPercentage'] = float(value) / 100 if float(value) > 1 else float(value)
                                elif i == 1:  # Institutions percentage
                                    if isinstance(value, str) and '%' in value:
                                        major_holders_data['institutionsPercentage'] = float(value.replace('%', '').strip()) / 100
                                    elif isinstance(value, (float, int, np.number)):
                                        major_holders_data['institutionsPercentage'] = float(value) / 100 if float(value) > 1 else float(value)
                                elif i == 2:  # Institutions float percentage
                                    if isinstance(value, str) and '%' in value:
                                        major_holders_data['institutionsFloatPercentage'] = float(value.replace('%', '').strip()) / 100
                                    elif isinstance(value, (float, int, np.number)):
                                        major_holders_data['institutionsFloatPercentage'] = float(value) / 100 if float(value) > 1 else float(value)
                                elif i == 3:  # Institutions count
                                    if isinstance(value, str):
                                        # Remove commas and convert to int
                                        major_holders_data['institutionsCount'] = int(value.replace(',', ''))
                                    else:
                                        major_holders_data['institutionsCount'] = int(value)
                        except Exception as e:
                            logger.warning(f"Error processing major holders row {i} for {ticker}: {str(e)}")
                    
                    holdings_data['majorHolders'] = major_holders_data
                    
                except Exception as e:
                    logger.warning(f"Error processing major holders for {ticker}: {str(e)}")
                    holdings_data['majorHolders'] = {}
            
            # Top institutional holders
            if institutional_holders is not None and not institutional_holders.empty:
                top_institutions = []
                for _, row in institutional_holders.iterrows():
                    try:
                        holder_data = {'holder': 'Unknown'}
                        
                        # Process each field safely
                        try:
                            if 'Holder' in row:
                                holder_data['holder'] = str(row['Holder'])
                        except:
                            pass
                            
                        try:
                            if 'Shares' in row:
                                shares_val = row['Shares']
                                if isinstance(shares_val, str):
                                    holder_data['shares'] = int(shares_val.replace(',', ''))
                                else:
                                    holder_data['shares'] = int(shares_val)
                        except:
                            pass
                            
                        try:
                            if 'Value' in row:
                                value_val = row['Value']
                                if isinstance(value_val, str):
                                    holder_data['value'] = float(value_val.replace(',', '').replace('$', ''))
                                else:
                                    holder_data['value'] = float(value_val)
                        except:
                            pass
                            
                        try:
                            pct_key = '% Out'
                            if pct_key in row:
                                pct_val = row[pct_key]
                                if isinstance(pct_val, str) and '%' in pct_val:
                                    holder_data['percentage'] = float(pct_val.replace('%', '').strip()) / 100
                                elif isinstance(pct_val, (float, int, np.number)):
                                    holder_data['percentage'] = float(pct_val) / 100 if float(pct_val) > 1 else float(pct_val)
                        except:
                            pass
                        
                        top_institutions.append(holder_data)
                    except Exception as e:
                        logger.warning(f"Error processing institutional holder row for {ticker}: {str(e)}")
                
                holdings_data['topInstitutions'] = top_institutions[:10]  # Limit to top 10
            
            # Top mutual fund holders
            if mutualfund_holders is not None and not mutualfund_holders.empty:
                top_funds = []
                for _, row in mutualfund_holders.iterrows():
                    try:
                        fund_data = {'holder': 'Unknown'}
                        
                        # Process each field safely
                        try:
                            if 'Holder' in row:
                                fund_data['holder'] = str(row['Holder'])
                        except:
                            pass
                            
                        try:
                            if 'Shares' in row:
                                shares_val = row['Shares']
                                if isinstance(shares_val, str):
                                    fund_data['shares'] = int(shares_val.replace(',', ''))
                                else:
                                    fund_data['shares'] = int(shares_val)
                        except:
                            pass
                            
                        try:
                            if 'Value' in row:
                                value_val = row['Value']
                                if isinstance(value_val, str):
                                    fund_data['value'] = float(value_val.replace(',', '').replace('$', ''))
                                else:
                                    fund_data['value'] = float(value_val)
                        except:
                            pass
                            
                        try:
                            pct_key = '% Out'
                            if pct_key in row:
                                pct_val = row[pct_key]
                                if isinstance(pct_val, str) and '%' in pct_val:
                                    fund_data['percentage'] = float(pct_val.replace('%', '').strip()) / 100
                                elif isinstance(pct_val, (float, int, np.number)):
                                    fund_data['percentage'] = float(pct_val) / 100 if float(pct_val) > 1 else float(pct_val)
                        except:
                            pass
                        
                        top_funds.append(fund_data)
                    except Exception as e:
                        logger.warning(f"Error processing mutual fund holder row for {ticker}: {str(e)}")
                
                holdings_data['topMutualFunds'] = top_funds[:10]  # Limit to top 10
            
            # Process earnings trend data from stock.info
            trend_data = {}
            try:
                # Extract earnings forecast data from stock.info
                if stock_info:
                    # Current year and next year EPS estimates
                    current_year_eps = stock_info.get('earningsPerShare')
                    forward_eps = stock_info.get('forwardEps')
                    trailing_eps = stock_info.get('trailingEps')
                    
                    # Growth estimates
                    earnings_growth = stock_info.get('earningsGrowth')
                    revenue_growth = stock_info.get('revenueGrowth')
                    
                    # Forward P/E and PEG ratios
                    forward_pe = stock_info.get('forwardPE')
                    peg_ratio = stock_info.get('pegRatio')
                    
                    if any([current_year_eps, forward_eps, trailing_eps, earnings_growth, revenue_growth]):
                        trend_data['earningsEstimates'] = {
                            'currentYearEPS': float(current_year_eps) if current_year_eps is not None else None,
                            'forwardEPS': float(forward_eps) if forward_eps is not None else None,
                            'trailingEPS': float(trailing_eps) if trailing_eps is not None else None,
                            'earningsGrowth': float(earnings_growth) if earnings_growth is not None else None,
                            'revenueGrowth': float(revenue_growth) if revenue_growth is not None else None,
                            'forwardPE': float(forward_pe) if forward_pe is not None else None,
                            'pegRatio': float(peg_ratio) if peg_ratio is not None else None
                        }
                    
                    # Extract earnings dates
                    earnings_date = stock_info.get('earningsTimestamp')
                    if earnings_date:
                        try:
                            trend_data['nextEarningsDate'] = datetime.datetime.fromtimestamp(earnings_date).strftime('%Y-%m-%d')
                        except:
                            pass
            except Exception as e:
                logger.warning(f"Error processing earnings trend data for {ticker}: {str(e)}")
            
            # Add key financial metrics from stock.info
            financial_metrics = {}
            if stock_info:
                try:
                    metrics = {
                        'marketCap': stock_info.get('marketCap'),
                        'trailingPE': stock_info.get('trailingPE'),
                        'priceToSales': stock_info.get('priceToSalesTrailing12Months'),
                        'priceToBook': stock_info.get('priceToBook'),
                        'enterpriseValue': stock_info.get('enterpriseValue'),
                        'enterpriseToRevenue': stock_info.get('enterpriseToRevenue'),
                        'enterpriseToEbitda': stock_info.get('enterpriseToEbitda'),
                        'beta': stock_info.get('beta'),
                        'fiftyTwoWeekHigh': stock_info.get('fiftyTwoWeekHigh'),
                        'fiftyTwoWeekLow': stock_info.get('fiftyTwoWeekLow'),
                        'dividendRate': stock_info.get('dividendRate'),
                        'dividendYield': stock_info.get('dividendYield'),
                        'payoutRatio': stock_info.get('payoutRatio'),
                        'profitMargins': stock_info.get('profitMargins'),
                        'operatingMargins': stock_info.get('operatingMargins'),
                        'returnOnAssets': stock_info.get('returnOnAssets'),
                        'returnOnEquity': stock_info.get('returnOnEquity'),
                        'revenuePerShare': stock_info.get('revenuePerShare'),
                        'debtToEquity': stock_info.get('debtToEquity'),
                        'currentRatio': stock_info.get('currentRatio'),
                        'quickRatio': stock_info.get('quickRatio')
                    }
                    
                    # Filter out None values and convert to float
                    financial_metrics = {}
                    for k, v in metrics.items():
                        if v is not None:
                            try:
                                financial_metrics[k] = float(v)
                            except:
                                pass
                except Exception as e:
                    logger.warning(f"Error processing financial metrics for {ticker}: {str(e)}")
            
            # Update DynamoDB with all the collected data
            self._update_earnings_data(
                ticker, 
                annual_earnings, 
                quarterly_earnings_list, 
                next_earnings,
                price_targets=price_targets,
                holdings_data=holdings_data,
                trend_data=trend_data,
                financial_metrics=financial_metrics
            )
            
            # Add a small delay to avoid rate limiting
            time.sleep(self.rate_limit_delay)
            
            return {
                "success": True,
                "ticker": ticker,
                "message": "Data updated successfully"
            }
            
        except Exception as e:
            logger.error(f"Error fetching earnings for {ticker}: {str(e)}")
            return {"success": False, "ticker": ticker, "error": str(e)}

    def _update_earnings_data(
        self, 
        ticker: str, 
        annual_earnings: List, 
        quarterly_earnings: List, 
        next_earnings: Optional[str] = None,
        price_targets: Optional[Dict] = None,
        holdings_data: Optional[Dict] = None,
        trend_data: Optional[Dict] = None,
        financial_metrics: Optional[Dict] = None
    ):
        """Update earnings data in DynamoDB"""
        try:
            update_expression = 'SET annualearnings = :a, quarterlyearnings = :q, #ts = :t'
            expression_attr_values = {
                ':a': DecimalEncoder.convert_to_decimal(annual_earnings),
                ':q': DecimalEncoder.convert_to_decimal(quarterly_earnings),
                ':t': datetime.datetime.now().isoformat()
            }
            
            # Add next earnings date if available
            if next_earnings:
                update_expression += ', nextEarningsDate = :n'
                expression_attr_values[':n'] = next_earnings
            
            # Add price targets if available
            if price_targets:
                update_expression += ', priceTargets = :pt'
                expression_attr_values[':pt'] = DecimalEncoder.convert_to_decimal(price_targets)
            
            # Add holdings data if available
            if holdings_data:
                update_expression += ', holdingsData = :hd'
                expression_attr_values[':hd'] = DecimalEncoder.convert_to_decimal(holdings_data)
            
            # Add trend data if available
            if trend_data:
                update_expression += ', trendData = :td'
                expression_attr_values[':td'] = DecimalEncoder.convert_to_decimal(trend_data)
            
            # Add financial metrics if available
            if financial_metrics:
                update_expression += ', financialMetrics = :fm'
                expression_attr_values[':fm'] = DecimalEncoder.convert_to_decimal(financial_metrics)
            
            self.earnings_table.update_item(
                Key={'stockId': ticker},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_attr_values,
                ExpressionAttributeNames={
                    '#ts': 'timestamp'
                }
            )
        except ClientError as e:
            logger.error(f"DynamoDB error updating earnings for {ticker}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error updating earnings for {ticker}: {str(e)}")
            raise

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function"""
    try:
        # Initialize handlers
        dynamo_handler = DynamoDBHandler()
        earnings_fetcher = EarningsDataFetcher()
        
        # Get portfolio tickers
        portfolio_response = dynamo_handler.get_primary_key_values("portfolio")
        if not portfolio_response['success']:
            raise ValueError("Failed to retrieve portfolio data")
            
        tickers = portfolio_response['key_values']
        logger.info(f"Processing {len(tickers)} unique tickers")
        
        # Fetch earnings data for each ticker
        results = []
        for ticker in tickers:
            result = earnings_fetcher.fetch_earnings(ticker)
            results.append(result)
            logger.info(f"Processed {ticker}: {'Success' if result['success'] else 'Failed'}")
            
        # Count successes and failures
        successes = sum(1 for r in results if r['success'])
        failures = sum(1 for r in results if not r['success'])
        
        logger.info(f"Processing complete: {successes} successful, {failures} failed")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing complete',
                'total_processed': len(results),
                'successful': successes,
                'failed': failures
            })
        }
        
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }