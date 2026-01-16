# Google Ads API Integration - Implementation Status

**Last Updated:** January 16, 2026
**Status:** Blocked on Basic Access approval
**Next Action:** Apply for Basic Access, then complete EventBridge setup and test

---

## Executive Summary

The Google Keyword Planner integration is **fully built and deployed**. The Lambda is live in AWS but cannot execute because the developer token has "Explorer Access" which doesn't include Keyword Planner methods. Once Basic Access is approved, only two steps remain: create the EventBridge schedule and run end-to-end testing.

---

## Part 1: What Was Built

### 1.1 OAuth 2.0 Authentication Setup

**Completed:** Full OAuth flow to get refresh token for Google Ads API.

**Credentials stored in AWS Secrets Manager:**
- Secret Name: `chipi-rss-pipeline/google-ads`
- Contents:
  ```json
  {
    "client_id": "[STORED IN AWS SECRETS MANAGER]",
    "client_secret": "[STORED IN AWS SECRETS MANAGER]",
    "refresh_token": "[STORED IN AWS SECRETS MANAGER]",
    "developer_token": "[STORED IN AWS SECRETS MANAGER]",
    "login_customer_id": "9236040286",
    "chipi_customer_id": "1229976456"
  }
  ```
- **To retrieve credentials:** `aws secretsmanager get-secret-value --secret-id chipi-rss-pipeline/google-ads`

**Account IDs:**
- Manager Account: `9236040286` (used for API calls)
- Chipi Account: `1229976456` (has active ads)

### 1.2 SEO Enricher Lambda

**Lambda Name:** `chipi-seo-enricher`
**Runtime:** Python 3.12
**Memory:** 512 MB
**Timeout:** 300 seconds (5 minutes)
**Region:** us-east-1

**Source Code Location:**
- Local: `lambdas/seo-enricher/lambda_function.py`
- GitHub: https://github.com/dealfanatics/rss-pipeline-lambda

**Dependencies:**
- `google-ads >= 24.0.0`
- `requests >= 2.28.0`
- Package size: 54 MB (deployed via S3)

**S3 Deployment Package:**
- Bucket: `chipi-lambda-deployments-221932176915`
- Key: `chipi-seo-enricher.zip`

**Environment Variables:**
```
AIRTABLE_BASE_ID=appE1zQXzgAky8OMJ
ARTICLES_TABLE=tblPCyNoy3oK3ao0h
RECORDS_PER_RUN=10
```

**IAM Role:** `arn:aws:iam::221932176915:role/chipi-rss-lambda-role`

### 1.3 Lambda Functionality

The Lambda performs these operations:

1. **Query Airtable** for records where:
   - `article_keywords` is not empty
   - `seo_target_keywords` is empty (not yet enriched)
   - Limit: 10 records per run

2. **Parse Keywords** from pipe-delimited `article_keywords` field

3. **Call Google Keyword Planner API:**
   - `GenerateKeywordHistoricalMetrics` - Get volume, competition, CPC
   - `GenerateKeywordIdeas` - Get related keyword suggestions

4. **Update Airtable** with enriched data:
   - `seo_target_keywords` - JSON array of keyword metrics
   - `seo_long_tail_keywords` - JSON array of related keywords

### 1.4 Airtable Schema (No Changes Needed)

Using existing fields in `articleExtractLibrary` table (`tblPCyNoy3oK3ao0h`):

| Field | Type | Content After Enrichment |
|-------|------|--------------------------|
| `article_keywords` | multilineText | Pipe-delimited keywords (input) |
| `seo_target_keywords` | multilineText | JSON: `[{keyword, volume, competition, competition_index, cpc_low, cpc_high}]` |
| `seo_long_tail_keywords` | multilineText | JSON: `[{keyword, volume, competition}]` |

### 1.5 Design Document

**File:** `docs/google-ads-api-design-document.md`

Comprehensive document created for Google's Basic Access application, including:
- Business background (Chipi.ai)
- System architecture diagrams
- Current Keyword Planner integration details
- Future roadmap (campaign performance management)
- API usage projections
- Security and compliance information

---

## Part 2: Current Deployment State

### 2.1 Lambda Status

```bash
# Verify Lambda exists and is active
aws lambda get-function --function-name chipi-seo-enricher \
  --query 'Configuration.{State:State,LastModified:LastModified}'
```

**Expected Output:**
```json
{
  "State": "Active",
  "LastModified": "2026-01-16T04:51:29.000+0000"
}
```

### 2.2 What Works Now

- ✅ Lambda deployed and executable
- ✅ Airtable query works (found records needing enrichment)
- ✅ Keyword parsing works (extracted 6 keywords from test article)
- ✅ Google Ads API connection works (authenticates successfully)
- ✅ Credentials retrieval from Secrets Manager works

### 2.3 What's Blocked

- ❌ `GenerateKeywordHistoricalMetrics` - Requires Basic Access
- ❌ `GenerateKeywordIdeas` - Requires Basic Access

**Error Message:**
```
This method is not allowed for use with explorer access.
Please apply for basic or standard access.
```

---

## Part 3: What Remains After Basic Access

### Step 1: Test the Lambda (5 minutes)

Once Basic Access is approved, run this command to test:

```bash
aws lambda invoke \
  --function-name chipi-seo-enricher \
  --payload '{}' \
  /tmp/seo-test-output.json \
  --log-type Tail \
  --query 'LogResult' \
  --output text | base64 -d
```

**Expected Success Output:**
```
[INFO] Starting SEO enrichment run
[INFO] Found X records needing SEO enrichment
[INFO] Processing: [article title]...
[INFO]   Found N keywords: [...]
[INFO]   Got metrics for N keywords
[INFO]   Got N related keywords
[INFO]   Successfully updated SEO data
[INFO] SEO enrichment complete: {'processed': X, 'failed': 0, 'total_found': X}
```

### Step 2: Verify Airtable Update (2 minutes)

Check that the test article now has SEO data:

```bash
# Using Airtable MCP tool or API
# Look for the Salesforce article that was used in testing
# Verify seo_target_keywords and seo_long_tail_keywords are populated
```

### Step 3: Create EventBridge Schedule (5 minutes)

The AWS user may not have EventBridge permissions. Try these commands or use AWS Console:

**Option A: AWS CLI (if permissions exist)**
```bash
# Create the schedule rule
aws events put-rule \
  --name chipi-seo-enricher-schedule \
  --schedule-expression "rate(2 hours)" \
  --state ENABLED \
  --description "Triggers SEO enrichment Lambda every 2 hours"

# Add Lambda as target
aws events put-targets \
  --rule chipi-seo-enricher-schedule \
  --targets "Id"="1","Arn"="arn:aws:lambda:us-east-1:221932176915:function:chipi-seo-enricher"

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
  --function-name chipi-seo-enricher \
  --statement-id EventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:us-east-1:221932176915:rule/chipi-seo-enricher-schedule
```

**Option B: AWS Console**
1. Go to Amazon EventBridge → Rules → Create rule
2. Name: `chipi-seo-enricher-schedule`
3. Schedule expression: `rate(2 hours)`
4. Target: Lambda function → `chipi-seo-enricher`
5. Save

### Step 4: Monitor First Scheduled Run (2 hours)

After EventBridge is set up, wait for the first scheduled execution and check logs:

```bash
aws logs tail /aws/lambda/chipi-seo-enricher --since 3h --format short
```

---

## Part 4: Files Reference

### GitHub Repository
- URL: https://github.com/dealfanatics/rss-pipeline-lambda
- Branch: main

### Project Structure
```
marketing-automation-pipeline/
├── lambdas/
│   ├── collector/
│   │   └── lambda_function.py      # RSS collection
│   ├── processor/
│   │   └── lambda_function.py      # Article extraction
│   └── seo-enricher/
│       ├── lambda_function.py      # Google Keyword Planner integration
│       └── requirements.txt        # Dependencies
├── docs/
│   ├── google-ads-api-design-document.md   # Basic Access application doc
│   ├── google-ads-integration-status.md    # This file
│   ├── extraction-prompt-draft.md
│   ├── extraction-prompt-feedback.md
│   └── schema-mapping-feedback.md
├── collector.zip
└── processor.zip
```

### AWS Resources
| Resource | ARN/ID |
|----------|--------|
| Collector Lambda | `chipi-rss-collector` |
| Processor Lambda | `chipi-rss-processor` |
| SEO Enricher Lambda | `chipi-seo-enricher` |
| SQS Queue | `https://sqs.us-east-1.amazonaws.com/221932176915/chipi-rss-articles` |
| IAM Role | `arn:aws:iam::221932176915:role/chipi-rss-lambda-role` |
| S3 Bucket | `chipi-lambda-deployments-221932176915` |
| Secrets | `chipi-rss-pipeline/airtable`, `chipi-rss-pipeline/google-ads`, `chipi-rss-pipeline/proxies` |

### Airtable
| Table | ID | Purpose |
|-------|-----|---------|
| RSS Sources | `tblHitPKDY321NQ6e` | Feed URLs and settings |
| Article Extract Library | `tblPCyNoy3oK3ao0h` | Extracted articles + SEO data |
| Failed Articles | `tbl4J5DyNqzdJwcve` | Processing failures |
| Base ID | `appE1zQXzgAky8OMJ` | - |

---

## Part 5: Troubleshooting

### If Lambda Fails After Basic Access

**Check 1: Verify developer token was updated**
```bash
aws secretsmanager get-secret-value \
  --secret-id chipi-rss-pipeline/google-ads \
  --query 'SecretString' --output text | python3 -c "import json,sys; print(json.load(sys.stdin)['developer_token'])"
```

**Check 2: Verify customer ID**
The Lambda uses `login_customer_id` (manager account) for Keyword Planner:
```python
customer_id = creds['login_customer_id']  # Should be 9236040286
```

**Check 3: Check CloudWatch logs**
```bash
aws logs tail /aws/lambda/chipi-seo-enricher --since 10m --format short
```

### If No Records Found

The query looks for records where:
- `article_keywords` is not empty
- `seo_target_keywords` is empty

If no records are found, either:
1. All records are already enriched, OR
2. No records have `article_keywords` populated

To reset a record for testing, clear its `seo_target_keywords` field in Airtable.

---

## Part 6: Quick Start Commands

**Test Lambda manually:**
```bash
aws lambda invoke --function-name chipi-seo-enricher --payload '{}' /tmp/out.json --log-type Tail --query 'LogResult' --output text | base64 -d
```

**Check Lambda status:**
```bash
aws lambda get-function --function-name chipi-seo-enricher --query 'Configuration.State'
```

**View recent logs:**
```bash
aws logs tail /aws/lambda/chipi-seo-enricher --since 1h --format short
```

**Redeploy Lambda (if code changes needed):**
```bash
cd /tmp/seo-enricher/package
python3 -c "import shutil; shutil.make_archive('/tmp/chipi-seo-enricher', 'zip', '.')"
aws s3 cp /tmp/chipi-seo-enricher.zip s3://chipi-lambda-deployments-221932176915/
aws lambda update-function-code --function-name chipi-seo-enricher \
  --s3-bucket chipi-lambda-deployments-221932176915 \
  --s3-key chipi-seo-enricher.zip
```

---

## Checklist: After Basic Access Approval

- [ ] Run test: `aws lambda invoke --function-name chipi-seo-enricher ...`
- [ ] Verify Airtable record updated with SEO data
- [ ] Create EventBridge rule (Console or CLI)
- [ ] Wait for first scheduled run (2 hours)
- [ ] Verify logs show successful processing
- [ ] Commit any final changes to GitHub

---

*This document serves as the handoff for resuming work after Basic Access is granted.*
