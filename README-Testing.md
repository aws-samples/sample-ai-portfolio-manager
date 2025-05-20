# Testing the AI Portfolio Manager

This guide provides instructions on how to test the AI Portfolio Manager after deployment.

## Prerequisites

1. The AI Portfolio Manager has been successfully deployed using the provided deployment script
2. You have verified your email addresses in Amazon SES
3. You have the name of your portfolio data S3 bucket (provided at the end of the deployment)

## Testing with Sample CSV Files

### 1. Create Sample Portfolio CSV File

Create a file named `portfolio.csv` with the following content:

```csv
stockId,companyName,price,quantity
AAPL,Apple Inc.,150.00,10
MSFT,Microsoft Corporation,300.00,5
AMZN,Amazon.com Inc.,3000.00,2
GOOGL,Alphabet Inc.,2500.00,3
TSLA,Tesla Inc.,700.00,8
```

This file contains a sample portfolio with 5 stocks.

### 2. Create Sample Risk Profile Questionnaire CSV File

Create a file named `profile.csv` with the following content:

```csv
question,answer
"What is your investment time horizon?","10+ years"
"How would you react to a 20% market drop?","I would see it as an opportunity to buy more"
"What percentage of your portfolio are you comfortable allocating to high-risk investments?","30%"
"What is your primary investment goal?","Growth with moderate risk"
"How much investment experience do you have?","5+ years"
```

This file contains sample responses to a risk profile questionnaire.

### 3. Upload the CSV Files to S3

Upload the CSV files to your portfolio data S3 bucket:

```bash
aws s3 cp portfolio.csv s3://YOUR_PORTFOLIO_BUCKET_NAME/portfolio.csv
aws s3 cp profile.csv s3://YOUR_PORTFOLIO_BUCKET_NAME/profile.csv
```

Replace `YOUR_PORTFOLIO_BUCKET_NAME` with the name of your portfolio data bucket that was created during deployment.

### 4. Verify Processing

The upload of these CSV files will trigger the following processes:

1. **Portfolio CSV Processing**:
   - The `stock-s3-processor` Lambda function will be triggered
   - The function will read the CSV file and store the data in the `portfolio` DynamoDB table
   - This will trigger the `stock-insight` Lambda function via DynamoDB Streams
   - The function will fetch stock data and store it in the `portfolio_stock_fundamentals` table

2. **Risk Profile CSV Processing**:
   - The `stock-risk-profile-processor` Lambda function will be triggered
   - The function will analyze the responses using Amazon Bedrock
   - The risk profile will be stored in the `portfolioprofile` DynamoDB table

### 5. Check DynamoDB Tables

You can verify that the data has been processed by checking the DynamoDB tables:

```bash
# Check portfolio table
aws dynamodb scan --table-name portfolio

# Check portfolio stock fundamentals table
aws dynamodb scan --table-name portfolio_stock_fundamentals

# Check portfolio profile table
aws dynamodb scan --table-name portfolioprofile
```

### 6. Test Scheduled Functions

The scheduled Lambda functions run on a weekly or monthly basis. To test them immediately:

```bash
# Test stock-insight function
aws lambda invoke --function-name stock-insight --payload '{}' response.json

# Test stock-earnings function
aws lambda invoke --function-name stock-earnings --payload '{}' response.json

# Test stock-recommendation function
aws lambda invoke --function-name stock-recommendation --payload '{}' response.json

# Test stock-alert function
aws lambda invoke --function-name stock-alert --payload '{}' response.json

```

### 7. Check Email Alerts

After running the `stock-alert` function, check the email address you provided for receiving alerts. You should receive an email with stock alerts and recommendations.

## Customizing the Test Data

You can customize the test data to include different stocks or risk profile responses:

### Portfolio CSV Format

The portfolio CSV file must have the following columns:
- `stockId`: The stock symbol (e.g., AAPL, MSFT)
- `companyName`: The company name
- `price`: The purchase price
- `quantity`: The number of shares

### Risk Profile CSV Format

The risk profile CSV file must have the following columns:
- `question`: The risk assessment question
- `answer`: The user's response to the question

## Troubleshooting

If you encounter issues during testing:

1. **Check CloudWatch Logs**:
   ```bash
   aws logs get-log-events --log-group-name /aws/lambda/stock-s3-processor --log-stream-name $(aws logs describe-log-streams --log-group-name /aws/lambda/stock-s3-processor --query 'logStreams[0].logStreamName' --output text)
   ```

2. **Verify S3 Bucket Notification Configuration**:
   ```bash
   aws s3api get-bucket-notification-configuration --bucket YOUR_PORTFOLIO_BUCKET_NAME
   ```

3. **Check Lambda Function Permissions**:
   ```bash
   aws lambda get-policy --function-name stock-s3-processor
   ```

4. **Verify SES Email Verification Status**:
   ```bash
   aws ses get-identity-verification-attributes --identities YOUR_EMAIL_ADDRESS
   ```