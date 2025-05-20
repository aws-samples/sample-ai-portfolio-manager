import boto3
import csv
import io
import os
import urllib.parse
def lambda_handler(event, context):
    # Initialize S3 and DynamoDB clients
    s3 = boto3.client('s3')
    dynamodb = boto3.resource('dynamodb')
    
    # S3 bucket and file details
    bucket_name = event['Records'][0]['s3']['bucket']['name']
    file_key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'], encoding='utf-8')
    
    # DynamoDB table name
    table_name = 'portfolio'
    table = dynamodb.Table(table_name)
    
    try:
        # Get the CSV file from S3
        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        csv_content = response['Body'].read().decode('utf-8')
        print(csv_content)
        # Parse CSV
        csv_file = io.StringIO(csv_content)
        csv_reader = csv.DictReader(csv_file)
        print(csv_reader)
        # Update DynamoDB
        for row in csv_reader:
            stock_id = row['stockId']
            companyName = row['companyName']
            price = row['price']
            quantity = row['quantity']
            print(f"Updating stockId: {stock_id}")
            table.update_item(
                Key={'stockId': stock_id},
                UpdateExpression='SET companyName=:companyName, price=:price, quantity=:quantity, updatedAt = :timestamp',
                ExpressionAttributeValues={
                    ':companyName': companyName,
                    ':price': price,
                    ':quantity': quantity,
                    ':timestamp': context.get_remaining_time_in_millis()
                }
            )
        
        return {
            'statusCode': 200,
            'body': 'Successfully updated DynamoDB table'
        }
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': f'Error updating DynamoDB table: {str(e)}'
        }
