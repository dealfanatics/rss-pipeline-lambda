# Chipi RSS Pipeline - Lambda Deployment Script
# Run this in PowerShell after downloading collector.zip and processor.zip

$REGION = "us-east-1"
$BUCKET = "chipi-lambda-deployments-221932176915"
$ROLE_ARN = "arn:aws:iam::221932176915:role/chipi-rss-lambda-role"

Write-Host "=== Chipi RSS Pipeline Deployment ===" -ForegroundColor Cyan

# Upload to S3
Write-Host "`nUploading deployment packages to S3..." -ForegroundColor Yellow
aws s3 cp collector.zip s3://$BUCKET/chipi-rss-collector.zip --region $REGION
aws s3 cp processor.zip s3://$BUCKET/chipi-rss-processor.zip --region $REGION

# Create Collector Lambda
Write-Host "`nCreating Collector Lambda..." -ForegroundColor Yellow
aws lambda create-function `
    --function-name chipi-rss-collector `
    --runtime python3.12 `
    --role $ROLE_ARN `
    --handler lambda_function.lambda_handler `
    --code S3Bucket=$BUCKET,S3Key=chipi-rss-collector.zip `
    --timeout 300 `
    --memory-size 512 `
    --architectures arm64 `
    --environment "Variables={AIRTABLE_BASE_ID=appE1zQXzgAky8OMJ,RSS_SOURCES_TABLE=tblHitPKDY321NQ6e,ARTICLES_TABLE=tblPCyNoy3oK3ao0h,SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/221932176915/chipi-rss-articles,RELEVANCE_THRESHOLD=60,BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0}" `
    --region $REGION

# Create Processor Lambda
Write-Host "`nCreating Processor Lambda..." -ForegroundColor Yellow
aws lambda create-function `
    --function-name chipi-rss-processor `
    --runtime python3.12 `
    --role $ROLE_ARN `
    --handler lambda_function.lambda_handler `
    --code S3Bucket=$BUCKET,S3Key=chipi-rss-processor.zip `
    --timeout 600 `
    --memory-size 512 `
    --architectures arm64 `
    --environment "Variables={AIRTABLE_BASE_ID=appE1zQXzgAky8OMJ,ARTICLES_TABLE=tblPCyNoy3oK3ao0h,BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0}" `
    --region $REGION

# Create SQS trigger for Processor
Write-Host "`nConnecting SQS trigger to Processor Lambda..." -ForegroundColor Yellow
aws lambda create-event-source-mapping `
    --function-name chipi-rss-processor `
    --event-source-arn arn:aws:sqs:us-east-1:221932176915:chipi-rss-articles `
    --batch-size 5 `
    --function-response-types ReportBatchItemFailures `
    --region $REGION

# Create EventBridge rule for hourly trigger
Write-Host "`nCreating hourly EventBridge trigger..." -ForegroundColor Yellow
aws events put-rule `
    --name chipi-rss-hourly-trigger `
    --schedule-expression "rate(1 hour)" `
    --state ENABLED `
    --region $REGION

aws lambda add-permission `
    --function-name chipi-rss-collector `
    --statement-id eventbridge-invoke `
    --action lambda:InvokeFunction `
    --principal events.amazonaws.com `
    --source-arn arn:aws:events:us-east-1:221932176915:rule/chipi-rss-hourly-trigger `
    --region $REGION

aws events put-targets `
    --rule chipi-rss-hourly-trigger `
    --targets "Id"="1","Arn"="arn:aws:lambda:us-east-1:221932176915:function:chipi-rss-collector" `
    --region $REGION

Write-Host "`n=== Deployment Complete ===" -ForegroundColor Green
Write-Host @"

Next steps:
1. Add RSS feeds to Airtable 'rssFeedSources' table
2. Test manually: aws lambda invoke --function-name chipi-rss-collector --region us-east-1 out.json
3. Check CloudWatch Logs for results

Manual step needed:
- In SQS Console, link the DLQ to the main queue:
  Main queue: chipi-rss-articles
  DLQ: chipi-rss-articles-dlq
  Max receives: 3
"@
