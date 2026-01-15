# Chipi RSS Pipeline - Deployment & Testing Guide

## Repository
**GitHub**: https://github.com/dealfanatics/rss-pipeline-lambda

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [AWS Infrastructure Setup](#aws-infrastructure-setup)
3. [Secrets Configuration](#secrets-configuration)
4. [Lambda Deployment](#lambda-deployment)
5. [Testing the Pipeline](#testing-the-pipeline)
6. [Monitoring & Troubleshooting](#monitoring--troubleshooting)

---

## Prerequisites

### Required Tools
```bash
# AWS CLI v2
aws --version  # Should be 2.x.x

# PowerShell (for deployment script)
pwsh --version  # Or use PowerShell on Windows
```

### Required AWS Permissions
Your IAM user/role needs these permissions:
- `lambda:*`
- `s3:*`
- `sqs:*`
- `events:*`
- `iam:PassRole`
- `secretsmanager:GetSecretValue`
- `bedrock:InvokeModel`

### Required Accounts
- **AWS Account** with Bedrock access enabled
- **Airtable Account** with a base containing:
  - `rssFeedSources` table (for RSS feed URLs)
  - `articleExtractLibrary` table (for processed articles)

---

## AWS Infrastructure Setup

### Step 1: Create S3 Bucket for Deployments
```bash
aws s3 mb s3://chipi-lambda-deployments-YOUR_ACCOUNT_ID --region us-east-1
```

### Step 2: Create SQS Queues
```bash
# Main queue
aws sqs create-queue \
    --queue-name chipi-rss-articles \
    --region us-east-1

# Dead letter queue
aws sqs create-queue \
    --queue-name chipi-rss-articles-dlq \
    --region us-east-1
```

### Step 3: Create IAM Role for Lambda
```bash
# Create the role
aws iam create-role \
    --role-name chipi-rss-lambda-role \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }'

# Attach policies
aws iam attach-role-policy \
    --role-name chipi-rss-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam attach-role-policy \
    --role-name chipi-rss-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/AmazonSQSFullAccess

aws iam attach-role-policy \
    --role-name chipi-rss-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite

aws iam attach-role-policy \
    --role-name chipi-rss-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess
```

---

## Secrets Configuration

### Step 4: Create Airtable Secret
```bash
aws secretsmanager create-secret \
    --name chipi-rss-pipeline/airtable \
    --secret-string '{"pat":"YOUR_AIRTABLE_PERSONAL_ACCESS_TOKEN"}' \
    --region us-east-1
```

### Step 5: Create Proxy Secret (Optional)
If using residential proxies for article fetching:
```bash
aws secretsmanager create-secret \
    --name chipi-rss-pipeline/proxies \
    --secret-string '{
        "proxies": [
            {"host": "proxy1.example.com", "port": "8080", "username": "user", "password": "pass"},
            {"host": "proxy2.example.com", "port": "8080", "username": "user", "password": "pass"}
        ]
    }' \
    --region us-east-1
```

---

## Lambda Deployment

### Step 6: Download Deployment Packages
```bash
git clone https://github.com/dealfanatics/rss-pipeline-lambda.git
cd rss-pipeline-lambda
```

### Step 7: Update Configuration
Edit `deploy-lambdas.ps1` and update these values:
```powershell
$REGION = "us-east-1"                                    # Your AWS region
$BUCKET = "chipi-lambda-deployments-YOUR_ACCOUNT_ID"     # Your S3 bucket
$ROLE_ARN = "arn:aws:iam::YOUR_ACCOUNT_ID:role/chipi-rss-lambda-role"
```

Also update the environment variables in the Lambda creation commands:
- `AIRTABLE_BASE_ID` - Your Airtable base ID
- `RSS_SOURCES_TABLE` - Table ID for RSS feeds
- `ARTICLES_TABLE` - Table ID for articles
- `SQS_QUEUE_URL` - Your SQS queue URL

### Step 8: Run Deployment Script
```powershell
# In PowerShell
./deploy-lambdas.ps1
```

**Or deploy manually with AWS CLI:**
```bash
# Upload to S3
aws s3 cp collector.zip s3://YOUR_BUCKET/chipi-rss-collector.zip
aws s3 cp processor.zip s3://YOUR_BUCKET/chipi-rss-processor.zip

# Create Collector Lambda
aws lambda create-function \
    --function-name chipi-rss-collector \
    --runtime python3.12 \
    --role arn:aws:iam::YOUR_ACCOUNT_ID:role/chipi-rss-lambda-role \
    --handler lambda_function.lambda_handler \
    --code S3Bucket=YOUR_BUCKET,S3Key=chipi-rss-collector.zip \
    --timeout 300 \
    --memory-size 512 \
    --architectures arm64 \
    --region us-east-1

# Create Processor Lambda
aws lambda create-function \
    --function-name chipi-rss-processor \
    --runtime python3.12 \
    --role arn:aws:iam::YOUR_ACCOUNT_ID:role/chipi-rss-lambda-role \
    --handler lambda_function.lambda_handler \
    --code S3Bucket=YOUR_BUCKET,S3Key=chipi-rss-processor.zip \
    --timeout 600 \
    --memory-size 512 \
    --architectures arm64 \
    --region us-east-1
```

---

## Testing the Pipeline

### Step 9: Add Test RSS Feed to Airtable

In your `rssFeedSources` table, add a record:
| Field | Value |
|-------|-------|
| feedName | TechCrunch AI |
| feedUrl | https://techcrunch.com/category/artificial-intelligence/feed/ |
| active | TRUE |

### Step 10: Test Collector Lambda
```bash
# Invoke the collector
aws lambda invoke \
    --function-name chipi-rss-collector \
    --region us-east-1 \
    --log-type Tail \
    --query 'LogResult' \
    --output text \
    response.json | base64 -d

# Check the response
cat response.json
```

**Expected Output:**
```json
{
    "statusCode": 200,
    "body": "{\"feeds_processed\": 1, \"articles_queued\": 5, \"duplicates_skipped\": 0}"
}
```

### Step 11: Verify SQS Messages
```bash
# Check queue depth
aws sqs get-queue-attributes \
    --queue-url https://sqs.us-east-1.amazonaws.com/YOUR_ACCOUNT_ID/chipi-rss-articles \
    --attribute-names ApproximateNumberOfMessages \
    --region us-east-1
```

### Step 12: Test Processor Lambda
```bash
# Create a test event (simulate SQS message)
cat > test-event.json << 'EOF'
{
    "Records": [{
        "messageId": "test-123",
        "body": "{\"url\":\"https://techcrunch.com/2024/01/15/sample-ai-article/\",\"title\":\"Test Article\",\"description\":\"A test article about AI\",\"source\":\"TechCrunch\",\"feed_name\":\"TechCrunch AI\",\"is_google_news\":false,\"relevance_score\":75}"
    }]
}
EOF

# Invoke processor with test event
aws lambda invoke \
    --function-name chipi-rss-processor \
    --payload file://test-event.json \
    --region us-east-1 \
    --log-type Tail \
    --query 'LogResult' \
    --output text \
    processor-response.json | base64 -d
```

### Step 13: Verify End-to-End Flow
1. Check your Airtable `articleExtractLibrary` table for new records
2. Verify extracted fields are populated:
   - `article_title`
   - `article_summary`
   - `key_themes`
   - `emotion_ideas_*` fields

---

## Monitoring & Troubleshooting

### View CloudWatch Logs
```bash
# Collector logs
aws logs tail /aws/lambda/chipi-rss-collector --follow --region us-east-1

# Processor logs
aws logs tail /aws/lambda/chipi-rss-processor --follow --region us-east-1
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Secret not found" | Secrets Manager secret missing | Create the secret per Step 4-5 |
| "Access Denied" to Bedrock | Bedrock not enabled | Enable Bedrock in AWS Console |
| "Rate limit exceeded" | Too many Airtable requests | Add delays or upgrade Airtable plan |
| Empty articles | Paywalled content | Configure proxy secrets |
| "Invalid model ID" | Wrong Bedrock model | Update `BEDROCK_MODEL_ID` env var |

### Check Lambda Configuration
```bash
# View collector config
aws lambda get-function-configuration \
    --function-name chipi-rss-collector \
    --region us-east-1

# View processor config
aws lambda get-function-configuration \
    --function-name chipi-rss-processor \
    --region us-east-1
```

### Test SQS Trigger Connection
```bash
# List event source mappings
aws lambda list-event-source-mappings \
    --function-name chipi-rss-processor \
    --region us-east-1
```

---

## Architecture Reference

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                    AWS Cloud                            │
                    │                                                         │
┌──────────┐        │  ┌─────────────┐     ┌─────────┐     ┌──────────────┐  │
│EventBridge│───────┼─▶│  Collector  │────▶│   SQS   │────▶│  Processor   │  │
│ (hourly)  │        │  │   Lambda    │     │  Queue  │     │    Lambda    │  │
└──────────┘        │  └──────┬──────┘     └─────────┘     └──────┬───────┘  │
                    │         │                                    │          │
                    │         │ ┌─────────────┐                   │          │
                    │         └▶│   Bedrock   │◀──────────────────┘          │
                    │           │  (AI/LLM)   │                              │
                    │           └─────────────┘                              │
                    │                                                         │
                    │         ┌─────────────────┐                            │
                    │         │ Secrets Manager │                            │
                    │         │ - Airtable PAT  │                            │
                    │         │ - Proxy config  │                            │
                    │         └─────────────────┘                            │
                    └─────────────────────────────────────────────────────────┘
                                            │
                                            ▼
                                    ┌───────────────┐
                                    │   Airtable    │
                                    │ - RSS Sources │
                                    │ - Articles    │
                                    └───────────────┘
```

---

## Quick Reference Commands

```bash
# Invoke collector manually
aws lambda invoke --function-name chipi-rss-collector --region us-east-1 out.json

# Check SQS queue depth
aws sqs get-queue-attributes --queue-url YOUR_QUEUE_URL --attribute-names All

# View recent logs
aws logs tail /aws/lambda/chipi-rss-collector --since 1h

# Update Lambda environment variables
aws lambda update-function-configuration \
    --function-name chipi-rss-collector \
    --environment "Variables={KEY=VALUE}"
```

---

## Support

- **Repository Issues**: https://github.com/dealfanatics/rss-pipeline-lambda/issues
- **AWS Lambda Docs**: https://docs.aws.amazon.com/lambda/
- **Airtable API**: https://airtable.com/developers/web/api/introduction
