import boto3
import csv
from io import StringIO
import json
import uuid
import os
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.client('dynamodb')

# Get environment variables
bucket_name = os.environ.get('bucket_name')
file_name = os.environ.get('file_name')
bedrock_model_id = os.environ.get('BEDROCK_MODEL_ID', 'amazon.nova-micro-v1:0')

def store_risk_profile(id, profile):
    """
    Store the generated risk profile in DynamoDB
    """
    try:
        dynamodb.put_item(
            TableName='portfolioprofile',
            Item={
                'userId': {'S': str(id)},
                'classification': {'S': profile['classification']},
                'reasoning': {'S': profile['reasoning']}
            }
        )
        logger.info(f"Successfully stored risk profile for user {id}")
    except Exception as e:
        logger.error(f"Error storing risk profile: {str(e)}")
        raise

def prompt_builder(prompt: str, token: int = 500) -> str:
    """
    Build the prompt for Bedrock model
    """
    body = {
        "inferenceConfig": {
            "max_new_tokens": token
        },
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": prompt
                    }
                ],
            }
        ],
    }
    return json.dumps(body)

def generate_risk_profile(responses):
    """
    Generate a risk profile based on user responses using Bedrock
    """
    try:
        prompt = f"""
            Based on the following user responses, generate a personalized investment risk profile.
            {responses}
            classify the user as:
            - Conservative (Low risk, prefers stable stocks).
            - Balanced (Moderate risk, mix of growth and value stocks).
            - Aggressive (High risk, growth stocks).
             Provide the recommendation in JSON format with:
                - classification (Conservative/Balanced/Aggressive)
                - reasoning (short explanation)

            You must respond with valid JSON only, without any additional text, explanations, or formatting.
            Respond with the JSON object only - no other text before or after.
            Do not include markdown formatting or code blocks.
            The response must be parseable by json.loads().
            """
        
        prompt_body = prompt_builder(prompt)
        
        logger.info(f"Invoking Bedrock model: {bedrock_model_id}")
        response = bedrock.invoke_model(
            body=prompt_body,
            modelId=bedrock_model_id,
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(response.get('body').read())
        logger.debug(f"Bedrock response: {response_body}")
        
        risk_profile = json.loads(response_body['output']['message']['content'][0]['text'])
        return risk_profile
        
    except Exception as e:
        logger.error(f"Error generating risk profile: {str(e)}")
        raise

def generate_consistent_userid(csv_content):
    """
    Generate a consistent userid based on the content of the CSV file
    """
    import hashlib
    # Create a hash of the CSV content to ensure the same file always generates the same userid
    hash_object = hashlib.md5(csv_content.encode())
    # Use the first 8 characters of the hash as the userid
    return f"user-{hash_object.hexdigest()[:8]}"

def process_csv_file(bucket, key):
    """
    Process the CSV file from S3
    """
    try:
        logger.info(f"Processing file {key} from bucket {bucket}")
        obj = s3.get_object(Bucket=bucket, Key=key)
        csv_content = obj['Body'].read().decode('utf-8')

        responses = {}
        csv_reader = csv.reader(StringIO(csv_content))
        
        # Skip header row
        next(csv_reader)
        
        for row in csv_reader:
            if len(row) >= 2:
                responses[row[0]] = row[1]
        
        if not responses:
            logger.warning("No valid responses found in CSV file")
            return False
            
        profile = generate_risk_profile(responses)
        # Generate a consistent userid based on the CSV content
        user_id = generate_consistent_userid(csv_content)
        store_risk_profile(user_id, profile)
        
        logger.info(f"Successfully processed profile for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error processing CSV file: {str(e)}")
        raise

def lambda_handler(event, context):
    """
    Lambda handler function
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        
        # Check if this is an S3 event
        if 'Records' in event and event['Records'][0].get('eventSource') == 'aws:s3':
            # Get bucket and key from the S3 event
            s3_event = event['Records'][0]['s3']
            bucket = s3_event['bucket']['name']
            key = s3_event['object']['key']
            
            # Process the uploaded file
            success = process_csv_file(bucket, key)
            
            return {
                'statusCode': 200,
                'body': json.dumps('Risk Profile generated and stored successfully' if success else 'No valid data found')
            }
        else:
            # Fallback to environment variables if not an S3 event
            success = process_csv_file(bucket_name, file_name)
            
            return {
                'statusCode': 200,
                'body': json.dumps('Risk Profile generated and stored successfully' if success else 'No valid data found')
            }
            
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error processing risk profile: {str(e)}')
        }