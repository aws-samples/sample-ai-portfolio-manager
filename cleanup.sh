#!/bin/bash
set -e

# Configuration
read -p "Enter the deployment bucket name to delete: " DEPLOYMENT_BUCKET
read -p "Enter the portfolio data bucket name to delete: " PORTFOLIO_BUCKET
read -p "Do you want to delete all resources? (yes/no): " CONFIRM_DELETE

if [ "$CONFIRM_DELETE" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
fi

echo "Starting cleanup process..."

# Step 1: Delete EventBridge Scheduler schedules
echo "Step 1: Deleting EventBridge Scheduler schedules..."
aws scheduler delete-schedule --name StockInsightSchedule || echo "StockInsightSchedule not found or already deleted"
aws scheduler delete-schedule --name StockEarningsSchedule || echo "StockEarningsSchedule not found or already deleted"
aws scheduler delete-schedule --name StockRecommendationSchedule || echo "StockRecommendationSchedule not found or already deleted"
aws scheduler delete-schedule --name StockAlertSchedule || echo "StockAlertSchedule not found or already deleted"

# Step 2: Delete Lambda event source mappings
echo "Step 2: Deleting Lambda event source mappings..."
# Get all event source mappings for stock-insight function
EVENT_SOURCE_MAPPINGS=$(aws lambda list-event-source-mappings --function-name stock-insight --query 'EventSourceMappings[*].UUID' --output text)

# Delete each event source mapping
for UUID in $EVENT_SOURCE_MAPPINGS; do
    echo "Deleting event source mapping: $UUID"
    aws lambda delete-event-source-mapping --uuid $UUID || echo "Event source mapping $UUID not found or already deleted"
done

# Step 3: Delete Lambda functions
echo "Step 3: Deleting Lambda functions..."
aws lambda delete-function --function-name stock-s3-processor || echo "stock-s3-processor not found or already deleted"
aws lambda delete-function --function-name stock-insight || echo "stock-insight not found or already deleted"
aws lambda delete-function --function-name stock-earnings || echo "stock-earnings not found or already deleted"
aws lambda delete-function --function-name stock-recommendation || echo "stock-recommendation not found or already deleted"
aws lambda delete-function --function-name stock-alert || echo "stock-alert not found or already deleted"
aws lambda delete-function --function-name stock-risk-profile-processor || echo "stock-risk-profile-processor not found or already deleted"

# Step 4: Delete Lambda layer
echo "Step 4: Deleting Lambda layer..."
# Get the latest version of the layer
LAYER_VERSION=$(aws lambda list-layer-versions --layer-name YfinanceRequestsLayer --query 'LayerVersions[0].Version' --output text)

if [ "$LAYER_VERSION" != "None" ]; then
    echo "Deleting YfinanceRequestsLayer version $LAYER_VERSION"
    aws lambda delete-layer-version --layer-name YfinanceRequestsLayer --version-number $LAYER_VERSION || echo "Layer version not found or already deleted"
fi

# Step 5: Delete DynamoDB tables
echo "Step 5: Deleting DynamoDB tables..."
aws dynamodb delete-table --table-name portfolio || echo "portfolio table not found or already deleted"
aws dynamodb delete-table --table-name portfolio_recommendation || echo "portfolio_recommendation table not found or already deleted"
aws dynamodb delete-table --table-name portfolio_stock_fundamentals || echo "portfolio_stock_fundamentals table not found or already deleted"
aws dynamodb delete-table --table-name portfolio_stock_trend || echo "portfolio_stock_trend table not found or already deleted"
aws dynamodb delete-table --table-name portfolio_earnings || echo "portfolio_earnings table not found or already deleted"
aws dynamodb delete-table --table-name portfolio_bias || echo "portfolio_bias table not found or already deleted"
aws dynamodb delete-table --table-name portfolioprofile || echo "portfolioprofile table not found or already deleted"

# Step 6: Delete IAM roles and policies
echo "Step 6: Deleting IAM roles and policies..."

# Delete EventBridge Scheduler role
echo "Deleting EventBridge Scheduler role policies..."
aws iam delete-role-policy --role-name EventBridgeSchedulerRole --policy-name EventBridgeSchedulerExecutionPolicy || echo "EventBridgeSchedulerExecutionPolicy not found or already deleted"

echo "Deleting EventBridge Scheduler role..."
aws iam delete-role --role-name EventBridgeSchedulerRole || echo "EventBridgeSchedulerRole not found or already deleted"

# Delete Lambda role
echo "Deleting Lambda role policies..."
aws iam delete-role-policy --role-name LambdaDynamoDBBedrockRole --policy-name BedrockInvokeModelPolicy || echo "BedrockInvokeModelPolicy not found or already deleted"

echo "Detaching managed policies from Lambda role..."
aws iam detach-role-policy --role-name LambdaDynamoDBBedrockRole --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole || echo "AWSLambdaBasicExecutionRole not detached"
aws iam detach-role-policy --role-name LambdaDynamoDBBedrockRole --policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess || echo "AmazonDynamoDBFullAccess not detached"
aws iam detach-role-policy --role-name LambdaDynamoDBBedrockRole --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess || echo "AmazonS3ReadOnlyAccess not detached"
aws iam detach-role-policy --role-name LambdaDynamoDBBedrockRole --policy-arn arn:aws:iam::aws:policy/AmazonSESFullAccess || echo "AmazonSESFullAccess not detached"

echo "Deleting Lambda role..."
aws iam delete-role --role-name LambdaDynamoDBBedrockRole || echo "LambdaDynamoDBBedrockRole not found or already deleted"

# Step 7: Empty and delete S3 buckets
if [ -n "$DEPLOYMENT_BUCKET" ]; then
    echo "Step 7: Emptying and deleting S3 buckets..."
    echo "Emptying deployment bucket: $DEPLOYMENT_BUCKET"
    aws s3 rm s3://$DEPLOYMENT_BUCKET --recursive || echo "Failed to empty deployment bucket or bucket not found"
    
    echo "Deleting deployment bucket: $DEPLOYMENT_BUCKET"
    aws s3api delete-bucket --bucket $DEPLOYMENT_BUCKET || echo "Failed to delete deployment bucket or bucket not found"
fi

if [ -n "$PORTFOLIO_BUCKET" ]; then
    echo "Emptying portfolio bucket: $PORTFOLIO_BUCKET"
    aws s3 rm s3://$PORTFOLIO_BUCKET --recursive || echo "Failed to empty portfolio bucket or bucket not found"
    
    echo "Deleting portfolio bucket: $PORTFOLIO_BUCKET"
    aws s3api delete-bucket --bucket $PORTFOLIO_BUCKET || echo "Failed to delete portfolio bucket or bucket not found"
fi

# Step 8: Clean up local deployment files
echo "Step 8: Cleaning up local deployment files..."
rm -rf deployment lambda-layer.zip *.json

echo "Cleanup complete!"
echo "Note: If you verified email addresses in SES, they will remain verified unless you manually delete them."
echo "To delete verified email identities, use: aws ses delete-identity --identity your-email@example.com"