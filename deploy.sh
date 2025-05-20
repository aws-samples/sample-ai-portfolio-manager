#!/bin/bash
set -e

# Configuration
TIMESTAMP=$(date +%s)
DEPLOYMENT_BUCKET="ai-portfolio-deployment-$TIMESTAMP"
PORTFOLIO_BUCKET="ai-portfolio-data-$TIMESTAMP"
REGION=$(aws configure get region)
if [ -z "$REGION" ]; then
    REGION="us-east-1"  # Default region if not configured
fi

# Collect email addresses for SES validation and Lambda alerts
read -p "Enter email address for sending alerts (will be verified in SES): " SENDER_EMAIL
read -p "Enter email address for receiving alerts (will be verified in SES): " RECIPIENT_EMAIL
read -p "Enter Bedrock model ID (default: amazon.nova-micro-v1:0): " BEDROCK_MODEL_ID
BEDROCK_MODEL_ID=${BEDROCK_MODEL_ID:-"amazon.nova-micro-v1:0"}
BEDROCK_MAX_TOKENS=1000
BEDROCK_TEMPERATURE=0.0
BEDROCK_TOP_P=0.9

if [ -z "$SENDER_EMAIL" ] || [ -z "$RECIPIENT_EMAIL" ]; then
    echo "Error: Email addresses cannot be empty"
    exit 1
fi

echo "Deploying AI Portfolio Manager to region: $REGION"
echo "Using deployment bucket: $DEPLOYMENT_BUCKET"
echo "Using portfolio data bucket: $PORTFOLIO_BUCKET"
echo "Sender email: $SENDER_EMAIL"
echo "Recipient email: $RECIPIENT_EMAIL"
echo "Bedrock model ID: $BEDROCK_MODEL_ID"

# Step 1: Create S3 buckets
echo "Step 1: Creating S3 buckets..."
echo "Creating deployment bucket..."
MAX_RETRIES=3
RETRY_COUNT=0
BUCKET_CREATED=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ] && [ "$BUCKET_CREATED" = false ]; do
    if [ $RETRY_COUNT -gt 0 ]; then
        # Generate a new bucket name with additional randomness
        DEPLOYMENT_BUCKET="ai-portfolio-deployment-$TIMESTAMP-$(openssl rand -hex 4)"
        echo "Retrying with new deployment bucket name: $DEPLOYMENT_BUCKET"
    fi
    
    if aws s3api create-bucket \
        --bucket $DEPLOYMENT_BUCKET \
        --region $REGION \
        $(if [ "$REGION" != "us-east-1" ]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi) 2>/dev/null; then
        BUCKET_CREATED=true
        echo "Successfully created deployment bucket: $DEPLOYMENT_BUCKET"
        
        # Apply HTTPS-only policy to deployment bucket
        echo "Applying HTTPS-only bucket policy to deployment bucket..."
        cat > deployment-https-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EnforceHTTPS",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::$DEPLOYMENT_BUCKET",
        "arn:aws:s3:::$DEPLOYMENT_BUCKET/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    }
  ]
}
EOF
        aws s3api put-bucket-policy --bucket $DEPLOYMENT_BUCKET --policy file://deployment-https-policy.json
        echo "Applied HTTPS-only policy to $DEPLOYMENT_BUCKET"
    else
        RETRY_COUNT=$((RETRY_COUNT+1))
        if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
            echo "Failed to create deployment bucket after $MAX_RETRIES attempts."
            echo "Please try again with a different bucket name."
            exit 1
        fi
    fi
done

echo "Creating portfolio data bucket..."
RETRY_COUNT=0
BUCKET_CREATED=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ] && [ "$BUCKET_CREATED" = false ]; do
    if [ $RETRY_COUNT -gt 0 ]; then
        # Generate a new bucket name with additional randomness
        PORTFOLIO_BUCKET="ai-portfolio-data-$TIMESTAMP-$(openssl rand -hex 4)"
        echo "Retrying with new portfolio bucket name: $PORTFOLIO_BUCKET"
    fi
    
    if aws s3api create-bucket \
        --bucket $PORTFOLIO_BUCKET \
        --region $REGION \
        $(if [ "$REGION" != "us-east-1" ]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi) 2>/dev/null; then
        BUCKET_CREATED=true
        echo "Successfully created portfolio bucket: $PORTFOLIO_BUCKET"
        
        # Create folders (prefixes) in the portfolio bucket
        echo "Creating folders in the portfolio bucket..."
        aws s3api put-object --bucket $PORTFOLIO_BUCKET --key profile/
        aws s3api put-object --bucket $PORTFOLIO_BUCKET --key portfolio/
        echo "Created profile/ and portfolio/ folders in $PORTFOLIO_BUCKET"
        
        # Apply bucket policy to enforce HTTPS-only access
        echo "Applying HTTPS-only bucket policy..."
        cat > https-only-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EnforceHTTPS",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::$PORTFOLIO_BUCKET",
        "arn:aws:s3:::$PORTFOLIO_BUCKET/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    }
  ]
}
EOF
        aws s3api put-bucket-policy --bucket $PORTFOLIO_BUCKET --policy file://https-only-policy.json
        echo "Applied HTTPS-only policy to $PORTFOLIO_BUCKET"
    else
        RETRY_COUNT=$((RETRY_COUNT+1))
        if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
            echo "Failed to create portfolio bucket after $MAX_RETRIES attempts."
            echo "Please try again with a different bucket name."
            exit 1
        fi
    fi
done

# Step 2: Create IAM roles
echo "Step 2: Creating IAM roles..."

# Create Lambda execution role
echo "Creating Lambda execution role..."
cat > lambda-role-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
    --role-name LambdaDynamoDBBedrockRole \
    --assume-role-policy-document file://lambda-role-trust-policy.json

# Attach managed policies to Lambda role
echo "Attaching managed policies to Lambda role..."
echo "NOTE: For production use, replace these broad permissions with more restrictive custom policies"
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# NOTE: For production, use a custom policy with only the specific DynamoDB actions and tables needed
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess

# NOTE: For production, use a custom policy with only the specific S3 buckets and actions needed
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess

# NOTE: For production, use a custom policy with only the specific SES actions needed
aws iam attach-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-arn arn:aws:iam::aws:policy/AmazonSESFullAccess

# Create custom policy for Bedrock access
echo "Creating custom policy for Bedrock access..."
echo "NOTE: For production, restrict the Resource to specific model ARNs instead of '*'"
cat > bedrock-policy.json << EOF
{
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
}
EOF

aws iam put-role-policy \
    --role-name LambdaDynamoDBBedrockRole \
    --policy-name BedrockInvokeModelPolicy \
    --policy-document file://bedrock-policy.json

# Create EventBridge Scheduler role
echo "Creating EventBridge Scheduler role..."
cat > scheduler-role-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "scheduler.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
    --role-name EventBridgeSchedulerRole \
    --assume-role-policy-document file://scheduler-role-trust-policy.json

# Wait for roles to propagate
echo "Waiting for IAM roles to propagate..."
sleep 10

# Step 3: Create DynamoDB tables
echo "Step 3: Creating DynamoDB tables..."

echo "Creating portfolio table..."
aws dynamodb create-table \
    --table-name portfolio \
    --attribute-definitions AttributeName=stockId,AttributeType=S \
    --key-schema AttributeName=stockId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES

echo "Creating portfolio_recommendation table..."
aws dynamodb create-table \
    --table-name portfolio_recommendation \
    --attribute-definitions AttributeName=stockId,AttributeType=S \
    --key-schema AttributeName=stockId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

echo "Creating portfolio_stock_fundamentals table..."
aws dynamodb create-table \
    --table-name portfolio_stock_fundamentals \
    --attribute-definitions AttributeName=stockId,AttributeType=S \
    --key-schema AttributeName=stockId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

echo "Creating portfolio_stock_trend table..."
aws dynamodb create-table \
    --table-name portfolio_stock_trend \
    --attribute-definitions AttributeName=stockId,AttributeType=S \
    --key-schema AttributeName=stockId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

echo "Creating portfolio_earnings table..."
aws dynamodb create-table \
    --table-name portfolio_earnings \
    --attribute-definitions AttributeName=stockId,AttributeType=S \
    --key-schema AttributeName=stockId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

echo "Creating portfolio_bias table..."
aws dynamodb create-table \
    --table-name portfolio_bias \
    --attribute-definitions AttributeName=userId,AttributeType=S \
    --key-schema AttributeName=userId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

echo "Creating portfolioprofile table..."
aws dynamodb create-table \
    --table-name portfolioprofile \
    --attribute-definitions AttributeName=userId,AttributeType=S \
    --key-schema AttributeName=userId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

# Step 4: Create Lambda layer
echo "Step 4: Creating Lambda layer..."

# Create temporary directory for packaging
mkdir -p deployment/lambda-layer/python

# Install dependencies for Lambda layer
echo "Installing dependencies for Lambda layer..."
pip install yfinance requests -t deployment/lambda-layer/python

# Create Lambda layer zip
echo "Creating Lambda layer zip..."
cd deployment/lambda-layer
zip -r ../../lambda-layer.zip .
cd ../..

# Upload Lambda layer to S3
echo "Uploading Lambda layer to S3..."
aws s3 cp lambda-layer.zip s3://$DEPLOYMENT_BUCKET/lambda-layer.zip

# Create Lambda layer
echo "Creating Lambda layer in AWS..."
LAYER_VERSION=$(aws lambda publish-layer-version \
    --layer-name YfinanceRequestsLayer \
    --description "Layer for yfinance and requests" \
    --license-info MIT \
    --content S3Bucket=$DEPLOYMENT_BUCKET,S3Key=lambda-layer.zip \
    --compatible-runtimes python3.8 python3.9 \
    --query 'LayerVersionArn' \
    --output text)

echo "Created Lambda layer: $LAYER_VERSION"

# Step 5: Package and deploy Lambda functions
echo "Step 5: Packaging and deploying Lambda functions..."

# Package Lambda functions
echo "Packaging Lambda functions..."
cd handlers
for file in *.py; do
    function_name="${file%.py}"
    echo "Packaging $function_name..."
    zip -r "../deployment/$function_name.zip" "$file"
done
cd ..

# Get the Lambda role ARN
LAMBDA_ROLE_ARN=$(aws iam get-role --role-name LambdaDynamoDBBedrockRole --query 'Role.Arn' --output text)

# Create Lambda functions
echo "Creating Lambda functions..."

# stock-s3-processor function
echo "Creating stock-s3-processor function..."
aws lambda create-function \
    --function-name stock-s3-processor \
    --runtime python3.9 \
    --role $LAMBDA_ROLE_ARN \
    --handler stock-s3-processor.lambda_handler \
    --zip-file fileb://deployment/stock-s3-processor.zip \
    --timeout 120 \
    --layers $LAYER_VERSION \
    --environment "Variables={S3_BUCKET=$PORTFOLIO_BUCKET}"

# stock-insight function
echo "Creating stock-insight function..."
aws lambda create-function \
    --function-name stock-insight \
    --runtime python3.9 \
    --role $LAMBDA_ROLE_ARN \
    --handler stock-insight.lambda_handler \
    --zip-file fileb://deployment/stock-insight.zip \
    --timeout 120 \
    --layers $LAYER_VERSION

# stock-earnings function
echo "Creating stock-earnings function..."
aws lambda create-function \
    --function-name stock-earnings \
    --runtime python3.9 \
    --role $LAMBDA_ROLE_ARN \
    --handler stock-earnings.lambda_handler \
    --zip-file fileb://deployment/stock-earnings.zip \
    --timeout 120 \
    --layers $LAYER_VERSION

# stock-recommendation function
echo "Creating stock-recommendation function..."
aws lambda create-function \
    --function-name stock-recommendation \
    --runtime python3.9 \
    --role $LAMBDA_ROLE_ARN \
    --handler stock-recommendation.lambda_handler \
    --zip-file fileb://deployment/stock-recommendation.zip \
    --timeout 120 \
    --layers $LAYER_VERSION \
    --environment "Variables={BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID,BEDROCK_MAX_TOKENS=$BEDROCK_MAX_TOKENS,BEDROCK_TEMPERATURE=$BEDROCK_TEMPERATURE,BEDROCK_TOP_P=$BEDROCK_TOP_P}"

# stock-alert function
echo "Creating stock-alert function..."
aws lambda create-function \
    --function-name stock-alert \
    --runtime python3.9 \
    --role $LAMBDA_ROLE_ARN \
    --handler stock-alert.lambda_handler \
    --zip-file fileb://deployment/stock-alert.zip \
    --timeout 120 \
    --environment "Variables={SENDER_EMAIL=$SENDER_EMAIL,RECIPIENT_EMAIL=$RECIPIENT_EMAIL}"

# stock-risk-profile-processor function
echo "Creating stock-risk-profile-processor function..."
aws lambda create-function \
    --function-name stock-risk-profile-processor \
    --runtime python3.9 \
    --role $LAMBDA_ROLE_ARN \
    --handler stock-risk-profile-processor.lambda_handler \
    --zip-file fileb://deployment/stock-risk-profile-processor.zip \
    --timeout 120 \
    --environment "Variables={BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID,bucket_name=$PORTFOLIO_BUCKET,file_name=profile.csv}"

# Step 6: Configure Lambda triggers and permissions
echo "Step 6: Configuring Lambda triggers and permissions..."

# Get the portfolio table stream ARN
PORTFOLIO_TABLE_STREAM_ARN=$(aws dynamodb describe-table --table-name portfolio --query 'Table.LatestStreamArn' --output text)

# Create event source mapping for DynamoDB stream
echo "Creating event source mapping for DynamoDB stream..."
aws lambda create-event-source-mapping \
    --function-name stock-insight \
    --event-source $PORTFOLIO_TABLE_STREAM_ARN \
    --batch-size 100 \
    --starting-position LATEST

# Add S3 bucket notification permission for stock-s3-processor
echo "Adding S3 bucket notification permission for stock-s3-processor..."
ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)
aws lambda add-permission \
    --function-name stock-s3-processor \
    --statement-id s3-permission \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn arn:aws:s3:::$PORTFOLIO_BUCKET \
    --source-account $ACCOUNT_ID

# Add S3 bucket notification permission for stock-risk-profile-processor
echo "Adding S3 bucket notification permission for stock-risk-profile-processor..."
aws lambda add-permission \
    --function-name stock-risk-profile-processor \
    --statement-id s3-permission \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn arn:aws:s3:::$PORTFOLIO_BUCKET \
    --source-account $ACCOUNT_ID

# Configure S3 bucket notification for CSV files
echo "Configuring S3 bucket notification for CSV files..."
LAMBDA_ARN=$(aws lambda get-function --function-name stock-s3-processor --query 'Configuration.FunctionArn' --output text)
RISK_PROFILE_LAMBDA_ARN=$(aws lambda get-function --function-name stock-risk-profile-processor --query 'Configuration.FunctionArn' --output text)

# Fix: Use separate notification configurations for each Lambda function to avoid conflicts
echo "Configuring S3 bucket notification for stock-s3-processor (regular CSV files)..."

cat > notification-config-combined.json << EOF
{
    "LambdaFunctionConfigurations": [
        {
            "LambdaFunctionArn": "$LAMBDA_ARN",
            "Events": ["s3:ObjectCreated:*"],
            "Filter": {
                "Key": {
                    "FilterRules": [
                        {
                            "Name": "prefix",
                            "Value": "portfolio/"
                        },
                        {
                            "Name": "suffix",
                            "Value": ".csv"
                        }
                    ]
                }
            }
        },
        {
            "LambdaFunctionArn": "$RISK_PROFILE_LAMBDA_ARN",
            "Events": ["s3:ObjectCreated:*"],
            "Filter": {
                "Key": {
                    "FilterRules": [
                        {
                            "Name": "prefix",
                            "Value": "profile/"
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
}
EOF

# Apply the combined notification configuration
echo "Applying combined S3 bucket notification configuration..."
aws s3api put-bucket-notification-configuration \
    --bucket $PORTFOLIO_BUCKET \
    --notification-configuration file://notification-config-combined.json

# Step 7: Create EventBridge Scheduler schedules
echo "Step 7: Creating EventBridge Scheduler schedules..."

# Get the EventBridge Scheduler role ARN
SCHEDULER_ROLE_ARN=$(aws iam get-role --role-name EventBridgeSchedulerRole --query 'Role.Arn' --output text)

# Create scheduler policy to allow invoking Lambda functions
echo "Creating scheduler policy to allow invoking Lambda functions..."
STOCK_INSIGHT_ARN=$(aws lambda get-function --function-name stock-insight --query 'Configuration.FunctionArn' --output text)
STOCK_EARNINGS_ARN=$(aws lambda get-function --function-name stock-earnings --query 'Configuration.FunctionArn' --output text)
STOCK_RECOMMENDATION_ARN=$(aws lambda get-function --function-name stock-recommendation --query 'Configuration.FunctionArn' --output text)
STOCK_ALERT_ARN=$(aws lambda get-function --function-name stock-alert --query 'Configuration.FunctionArn' --output text)

cat > scheduler-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": [
        "$STOCK_INSIGHT_ARN",
        "$STOCK_EARNINGS_ARN",
        "$STOCK_RECOMMENDATION_ARN",
        "$STOCK_ALERT_ARN"
      ]
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name EventBridgeSchedulerRole \
    --policy-name EventBridgeSchedulerExecutionPolicy \
    --policy-document file://scheduler-policy.json

# Wait for policy to propagate
echo "Waiting for IAM policy to propagate..."
sleep 10

# Create schedules
echo "Creating EventBridge schedules..."

# Stock Insight Schedule
echo "Creating StockInsightSchedule..."
cat > stock-insight-schedule.json << EOF
{
  "Name": "StockInsightSchedule",
  "ScheduleExpression": "cron(00 06 ? * MON *)",
  "FlexibleTimeWindow": {
    "Mode": "OFF"
  },
  "Target": {
    "Arn": "$STOCK_INSIGHT_ARN",
    "RoleArn": "$SCHEDULER_ROLE_ARN"
  }
}
EOF

aws scheduler create-schedule \
    --cli-input-json file://stock-insight-schedule.json

# Stock Earnings Schedule
echo "Creating StockEarningsSchedule..."
cat > stock-earnings-schedule.json << EOF
{
  "Name": "StockEarningsSchedule",
  "ScheduleExpression": "cron(00 07 ? * MON *)",
  "FlexibleTimeWindow": {
    "Mode": "OFF"
  },
  "Target": {
    "Arn": "$STOCK_EARNINGS_ARN",
    "RoleArn": "$SCHEDULER_ROLE_ARN"
  }
}
EOF

aws scheduler create-schedule \
    --cli-input-json file://stock-earnings-schedule.json

# Stock Recommendation Schedule
echo "Creating StockRecommendationSchedule..."
cat > stock-recommendation-schedule.json << EOF
{
  "Name": "StockRecommendationSchedule",
  "ScheduleExpression": "cron(00 08 ? * MON *)",
  "FlexibleTimeWindow": {
    "Mode": "OFF"
  },
  "Target": {
    "Arn": "$STOCK_RECOMMENDATION_ARN",
    "RoleArn": "$SCHEDULER_ROLE_ARN"
  }
}
EOF

aws scheduler create-schedule \
    --cli-input-json file://stock-recommendation-schedule.json

# Stock Alert Schedule
echo "Creating StockAlertSchedule..."
cat > stock-alert-schedule.json << EOF
{
  "Name": "StockAlertSchedule",
  "ScheduleExpression": "cron(00 09 ? * MON *)",
  "FlexibleTimeWindow": {
    "Mode": "OFF"
  },
  "Target": {
    "Arn": "$STOCK_ALERT_ARN",
    "RoleArn": "$SCHEDULER_ROLE_ARN"
  }
}
EOF

aws scheduler create-schedule \
    --cli-input-json file://stock-alert-schedule.json

# Step 8: Verify SES email identities
echo "Step 8: Verifying SES email identities..."
aws ses verify-email-identity --email-address $SENDER_EMAIL
aws ses verify-email-identity --email-address $RECIPIENT_EMAIL
echo "Verification emails have been sent to $SENDER_EMAIL and $RECIPIENT_EMAIL."
echo "Please check your inbox and click the verification links."

# Clean up temporary files
echo "Cleaning up temporary files..."
rm -f lambda-role-trust-policy.json bedrock-policy.json scheduler-role-trust-policy.json notification-config.json
rm -f scheduler-policy.json stock-insight-schedule.json stock-earnings-schedule.json stock-recommendation-schedule.json stock-alert-schedule.json
rm -f https-only-policy.json deployment-https-policy.json

echo "Deployment complete!"
echo "Deployment bucket: $DEPLOYMENT_BUCKET"
echo "Portfolio data bucket: $PORTFOLIO_BUCKET"
echo ""
echo "IMPORTANT: Check your email and click the verification links to verify your email addresses in SES."
echo "IMPORTANT: See README-Testing.md for instructions on how to test the application with sample CSV files."