# AI Portfolio Manager

## Overview

This project leverages cloud computing and artificial intelligence to revolutionize stock market analysis. Our system automatically collects and processes comprehensive stock data, including fundamentals, technical indicators, and earnings reports. Using advanced AI models, it generates weekly investment recommendations and in-depth market analyses. Designed for individual investors, financial advisors, and portfolio managers, this tool provides data-driven insights to support informed investment decisions. Key features include automated data collection, AI-powered analysis, risk assessment, and historical pattern recognition. By combining real-time data processing with machine learning, we offer a scalable, accurate, and cost-effective solution for modern investment strategies. Our system aims to reduce research time, minimize emotional bias, and provide professional-grade analysis accessible to a wide range of users. Whether you're tracking market trends, evaluating investment opportunities, or managing a diverse portfolio, this tool offers valuable insights to enhance your investment process and potentially improve returns.

The entire system operates at an incredibly cost-effective rate of approximately $0.15 per month (Costs may vary based on actual usage patterns and data volumes) for managing a portfolio of 25 stocks, making it an economical solution for comprehensive stock analysis and AI-powered recommendations. This minimal cost includes all AWS services usage: Lambda executions, DynamoDB storage and operations, EventBridge scheduling, and Bedrock model invocations for weekly analyses.

> **IMPORTANT NOTE**: This is a Proof of Concept (POC) solution intended for demonstration purposes. For production deployments, it is strongly recommended to implement more restrictive IAM permissions following the principle of least privilege. The current deployment uses broad permissions (like DynamoDBFullAccess, SESFullAccess) to simplify the setup process, but these should be narrowed down to only the specific actions and resources required for your production environment.

## Architecture
![alt text](<design.png>)

## Deployment Options

This project offers two deployment options:

### Option 1: Direct Deployment with deploy.sh (Recommended)

The easiest way to deploy this project is using the provided deployment script.

#### Prerequisites
- AWS CLI installed and configured with appropriate permissions
- Python 3.8+ installed
- pip package manager

#### Deployment Steps

1. Make the deployment script executable:

```bash
chmod +x deploy.sh
```

2. Run the deployment script:

```bash
./deploy.sh
```

The script will:
- Prompt for email addresses for sending and receiving alerts
- Prompt for Bedrock model ID (default: amazon.nova-micro-v1:0)
- Create two separate S3 buckets for deployment and portfolio data
- Create folders in the portfolio bucket for profile and portfolio data
- Create necessary IAM roles and policies
- Create DynamoDB tables for storing portfolio data
- Install and package dependencies for the Lambda layer
- Package and deploy all Lambda functions
- Configure S3 bucket notifications for both profile and portfolio folders
- Set up EventBridge Scheduler for automated weekly processing
- Automatically verify your email addresses in SES

3. Check your email and click the verification links sent by Amazon SES.

4. Upload your data files to the appropriate folders in the S3 bucket:
   - Upload portfolio CSV files to the `portfolio/` folder
   - Upload risk profile CSV files to the `profile/` folder

The S3 bucket notifications are configured to automatically trigger the appropriate Lambda functions when files are uploaded to these folders.

### Option 2: Manual Deployment

If you prefer to deploy the resources manually or need more control over the deployment process, follow these steps:

#### 1. Create an IAM Role for Lambda Functions

First, create an IAM role that will be used by all Lambda functions:

```bash
aws iam create-role \
    --role-name LambdaDynamoDBBedrockRole \
    --assume-role-policy-document '{"Version": "2012-10-17","Statement": [{"Effect": "Allow","Principal": {"Service": "lambda.amazonaws.com"},"Action": "sts:AssumeRole"}]}'
```

#### 2. Attach Required Policies to the IAM Role

Attach the necessary policies to allow Lambda functions to access DynamoDB, S3, SES, and other required services:

```bash
# Basic Lambda execution permissions
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# DynamoDB access
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess

# S3 access
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess

# SES access for email alerts
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/AmazonSESFullAccess
```

#### 3. Create a Custom Policy for Amazon Bedrock

Create and attach a custom policy for Amazon Bedrock access:

```bash
aws iam create-policy \
    --policy-name BedrockInvokeModelPolicy \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream"
                ],
                "Resource": "*"
            }
        ]
    }'

aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::YOUR_ACCOUNT_ID:policy/BedrockInvokeModelPolicy
```

Make sure you have enabled access to the Amazon Bedrock models in your AWS account. For the stock-risk-profile-processor Lambda function, you'll need access to the Amazon Nova Micro model.

#### 4. Create S3 Buckets

Create two S3 buckets - one for deployment resources and another for portfolio data:

```bash
# Create deployment bucket
aws s3api create-bucket \
    --bucket your-deployment-bucket \
    --region YOUR_REGION \
    $(if [ "$REGION" != "us-east-1" ]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi)

# Create portfolio data bucket
aws s3api create-bucket \
    --bucket your-portfolio-data-bucket \
    --region YOUR_REGION \
    $(if [ "$REGION" != "us-east-1" ]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi)
```

For regions other than us-east-1, include the LocationConstraint parameter. For us-east-1, you can omit the create-bucket-configuration parameter.

#### 5. Create a Lambda Layer for Dependencies

Create a Lambda layer with the required Python packages:

```bash
# Create a directory and install the packages
mkdir -p lambda-layer/python
pip install yfinance requests -t lambda-layer/python

# Zip the layer
cd lambda-layer
zip -r ../lambda-layer.zip .

# Create the layer
aws lambda publish-layer-version \
    --layer-name YfinanceRequestsLayer \
    --description "Layer for yfinance and requests" \
    --zip-file fileb://../lambda-layer.zip \
    --compatible-runtimes python3.8 python3.9
```

#### 6. Clone the Repository

Download the project files:

```bash
git clone https://github.com/username/sample-ai-portfolio-manager.git
cd sample-ai-portfolio-manager
```

#### 7. Create the Lambda Functions

Create all the required Lambda functions:

```bash
# Navigate to the handlers directory
cd handlers

# Create stock-s3-processor Lambda function
zip stock-s3-processor.zip stock-s3-processor.py
aws lambda create-function \
    --function-name stock-s3-processor \
    --runtime python3.9 \
    --role arn:aws:iam::YOUR_ACCOUNT_ID:role/LambdaDynamoDBBedrockRole \
    --handler stock-s3-processor.lambda_handler \
    --zip-file fileb://stock-s3-processor.zip \
    --layers arn:aws:lambda:YOUR_REGION:YOUR_ACCOUNT_ID:layer:YfinanceRequestsLayer:1 \
    --environment "Variables={S3_BUCKET=your-portfolio-data-bucket}"

# Create other Lambda functions similarly...
```

#### 8. Set Up S3 Event Notifications

Configure S3 event notifications to trigger the StockRiskProfileProcessorFunction when a profile CSV file is uploaded:

```bash
# Get the Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name stock-risk-profile-processor --query 'Configuration.FunctionArn' --output text)

# Add permission for S3 to invoke the Lambda
aws lambda add-permission \
    --function-name stock-risk-profile-processor \
    --statement-id s3-trigger \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn arn:aws:s3:::your-portfolio-data-bucket

# Create notification configuration
aws s3api put-bucket-notification-configuration \
    --bucket your-portfolio-data-bucket \
    --notification-configuration '{
        "LambdaFunctionConfigurations": [
            {
                "Id": "ProfileProcessorTrigger",
                "LambdaFunctionArn": "'"$LAMBDA_ARN"'",
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {
                    "Key": {
                        "FilterRules": [
                            {
                                "Name": "prefix",
                                "Value": "profile"
                            },
                            {
                                "Name": "suffix",
                                "Value": ".csv"
                            }
                        ]
                    }
                }
            }
        ]
    }'
```

## Testing the Application

After deployment, refer to the [README-Testing.md](README-Testing.md) file for instructions on how to test the application with sample CSV files.

## Security Considerations

This project is provided as a Proof of Concept (POC) and uses broad IAM permissions to simplify the setup process. For production deployments, consider the following security best practices:

1. **Apply the Principle of Least Privilege**:
   - Replace the managed policies (DynamoDBFullAccess, SESFullAccess) with custom policies that grant only the specific permissions needed
   - Restrict S3 access to only the specific buckets used by the application
   - Limit DynamoDB access to only the specific tables and actions required

2. **Restrict Bedrock Model Access**:
   - Modify the Bedrock policy to specify exact model ARNs instead of using the wildcard "*"
   - Only allow access to the specific models needed for your application

3. **Implement Additional Security Controls**:
   - Enable encryption for S3 buckets and DynamoDB tables
   - Configure VPC endpoints for Lambda functions if needed
   - Implement proper logging and monitoring

## Configuration

### Bedrock Model Configuration

The stock recommendation and risk profile processor Lambda functions use Amazon Bedrock for AI-powered analysis. You can customize the model settings using environment variables:

- `BEDROCK_MODEL_ID`: The Bedrock model to use (default: amazon.nova-micro-v1:0)
- `BEDROCK_MAX_TOKENS`: Maximum number of tokens for the response (default: 1000)
- `BEDROCK_TEMPERATURE`: Controls randomness in the response (0.0-1.0, default: 0.0)
- `BEDROCK_TOP_P`: Controls diversity via nucleus sampling (0.0-1.0, default: 0.9)

To update these settings after deployment:

```bash
aws lambda update-function-configuration \
    --function-name stock-recommendation \
    --environment "Variables={BEDROCK_MODEL_ID=amazon.nova-micro-v1:0,BEDROCK_MAX_TOKENS=1000,BEDROCK_TEMPERATURE=0.0,BEDROCK_TOP_P=0.9}"

aws lambda update-function-configuration \
    --function-name stock-risk-profile-processor \
    --environment "Variables={BEDROCK_MODEL_ID=amazon.nova-micro-v1:0,bucket_name=your-portfolio-data-bucket,file_name=profile.csv}"
```

You can also modify the prompt templates directly in the `stock-recommendation.py` file in the CONFIG dictionary.

## Risk Profile Processing

The system uses a streamlined approach for risk profile processing:

1. Upload a CSV file named 'profile.csv' to the 'profile/' folder in your S3 bucket
2. The stock-risk-profile-processor Lambda function will automatically trigger and process the file
3. The function will:
   - Parse the CSV file to extract user responses
   - Use Amazon Bedrock to generate a personalized risk profile
   - Store the profile in the DynamoDB table
   - The risk profile will be used in subsequent stock recommendations

The CSV file should have the following format:
```
question,answer
"What is your investment time horizon?","5-10 years"
"How much risk are you willing to take?","Moderate"
...
```

## Important Notes

- Replace `YOUR_ACCOUNT_ID` with your AWS account ID
- Replace `YOUR_REGION` with your AWS region (e.g., us-east-1, us-west-2)
- Replace bucket names with globally unique names
- Replace email addresses with your verified email addresses
- S3 bucket names must be globally unique across all AWS accounts
- For SES, if your account is in the sandbox, you can only send emails to verified email addresses

## Troubleshooting

- Check CloudWatch Logs for any Lambda function errors
- Ensure all IAM permissions are correctly set up
- Verify that the S3 bucket notification is properly configured
- Make sure your email addresses are verified in SES before using email alerts
- Ensure you're uploading files to the correct folders in the S3 bucket:
  - Portfolio CSV files should go to the `portfolio/` folder
  - Risk profile CSV files should go to the `profile/` folder
- If you need to reconfigure S3 event notifications, you can rerun the deploy.sh script or manually update the notification configuration

## Cleanup

To delete all resources created by the deployment script, you can use the provided cleanup script:

```bash
chmod +x cleanup.sh
./cleanup.sh
```

The cleanup script will:
- Delete all Lambda functions
- Delete the Lambda layer
- Delete all DynamoDB tables
- Delete all EventBridge Scheduler schedules
- Delete IAM roles and policies
- Empty and delete the S3 buckets

If you prefer to clean up resources manually:

```bash
# Delete Lambda functions
aws lambda delete-function --function-name stock-s3-processor
aws lambda delete-function --function-name stock-insight
aws lambda delete-function --function-name stock-earnings
aws lambda delete-function --function-name stock-recommendation
aws lambda delete-function --function-name stock-alert
aws lambda delete-function --function-name stock-risk-profile-processor

# Delete Lambda layer
aws lambda delete-layer-version --layer-name YfinanceRequestsLayer --version-number 1

# Delete DynamoDB tables
aws dynamodb delete-table --table-name portfolio
aws dynamodb delete-table --table-name portfolio_recommendation
aws dynamodb delete-table --table-name portfolio_stock_fundamentals
aws dynamodb delete-table --table-name portfolio_stock_trend
aws dynamodb delete-table --table-name portfolio_earnings
aws dynamodb delete-table --table-name portfolio_bias
aws dynamodb delete-table --table-name portfolioprofile

# Delete S3 buckets
aws s3 rm s3://DEPLOYMENT_BUCKET_NAME --recursive
aws s3api delete-bucket --bucket DEPLOYMENT_BUCKET_NAME

aws s3 rm s3://PORTFOLIO_BUCKET_NAME --recursive
aws s3api delete-bucket --bucket PORTFOLIO_BUCKET_NAME
```

Replace DEPLOYMENT_BUCKET_NAME and PORTFOLIO_BUCKET_NAME with your actual bucket names.