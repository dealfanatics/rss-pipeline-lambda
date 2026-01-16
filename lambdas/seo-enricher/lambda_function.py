"""
Chipi SEO Enricher Lambda

Enriches extracted articles with Google Keyword Planner metrics.
Queries for search volume, competition, CPC, and related keywords.

Triggered by: EventBridge (every 2 hours)
Output: Updates Airtable seo_target_keywords and seo_long_tail_keywords
"""

import json
import os
import re
import logging
from typing import Optional

import boto3
import requests
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
secrets_client = boto3.client('secretsmanager', region_name='us-east-1')

# Constants
AIRTABLE_BASE_ID = os.environ.get('AIRTABLE_BASE_ID', 'appE1zQXzgAky8OMJ')
ARTICLES_TABLE = os.environ.get('ARTICLES_TABLE', 'tblPCyNoy3oK3ao0h')
RECORDS_PER_RUN = int(os.environ.get('RECORDS_PER_RUN', '10'))
RELATED_KEYWORDS_LIMIT = 5

# Cache
_secrets_cache = {}
_google_ads_client = None


def get_secret(secret_name: str) -> dict:
    """Retrieve secret from Secrets Manager with caching."""
    if secret_name not in _secrets_cache:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        _secrets_cache[secret_name] = json.loads(response['SecretString'])
    return _secrets_cache[secret_name]


def get_google_ads_client() -> GoogleAdsClient:
    """Initialize Google Ads client with credentials from Secrets Manager."""
    global _google_ads_client

    if _google_ads_client is None:
        creds = get_secret('chipi-rss-pipeline/google-ads')

        # Build credentials dict for GoogleAdsClient
        credentials = {
            'developer_token': creds['developer_token'],
            'client_id': creds['client_id'],
            'client_secret': creds['client_secret'],
            'refresh_token': creds['refresh_token'],
            'login_customer_id': creds['login_customer_id'],
            'use_proto_plus': True
        }

        _google_ads_client = GoogleAdsClient.load_from_dict(credentials)

    return _google_ads_client


def airtable_request(method: str, endpoint: str = '', data: dict = None, params: dict = None) -> dict:
    """Make authenticated request to Airtable API."""
    pat = get_secret('chipi-rss-pipeline/airtable')['pat']
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{ARTICLES_TABLE}{endpoint}"
    headers = {
        'Authorization': f'Bearer {pat}',
        'Content-Type': 'application/json'
    }

    response = requests.request(method, url, headers=headers, json=data, params=params, timeout=30)
    response.raise_for_status()
    return response.json() if response.text else {}


def get_records_needing_enrichment() -> list[dict]:
    """Fetch records that have keywords but no SEO data yet."""
    # Filter: article_keywords not empty AND seo_target_keywords is empty
    params = {
        'filterByFormula': "AND({article_keywords}!='', {seo_target_keywords}='')",
        'maxRecords': RECORDS_PER_RUN,
        'fields[]': ['article_keywords', 'article_title']
    }

    response = airtable_request('GET', params=params)
    records = response.get('records', [])
    logger.info(f"Found {len(records)} records needing SEO enrichment")
    return records


def parse_keywords(keywords_str: str) -> list[str]:
    """Parse pipe-delimited keywords string into list."""
    if not keywords_str:
        return []

    # Split by pipe and clean up
    keywords = [kw.strip() for kw in keywords_str.split('|') if kw.strip()]

    # Sanitize: remove special characters that might cause API issues
    sanitized = []
    for kw in keywords:
        clean = re.sub(r'[^\w\s\-]', '', kw).strip()
        if clean and len(clean) >= 2:
            sanitized.append(clean)

    return sanitized[:10]  # Limit to 10 keywords per record


def get_keyword_metrics(client: GoogleAdsClient, customer_id: str, keywords: list[str]) -> list[dict]:
    """
    Get historical metrics for specific keywords using Keyword Planner.
    Returns list of dicts with volume, competition, CPC data.
    """
    if not keywords:
        return []

    keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

    request = client.get_type("GenerateKeywordHistoricalMetricsRequest")
    request.customer_id = customer_id
    request.keywords.extend(keywords)

    # Set language and location (US English)
    request.language = "languageConstants/1000"  # English
    request.geo_target_constants.append("geoTargetConstants/2840")  # United States

    try:
        response = keyword_plan_idea_service.generate_keyword_historical_metrics(request=request)

        metrics = []
        for result in response.results:
            keyword_metrics = result.keyword_metrics

            # Convert micros to dollars
            cpc_low = keyword_metrics.low_top_of_page_bid_micros / 1_000_000 if keyword_metrics.low_top_of_page_bid_micros else 0
            cpc_high = keyword_metrics.high_top_of_page_bid_micros / 1_000_000 if keyword_metrics.high_top_of_page_bid_micros else 0

            metrics.append({
                'keyword': result.text,
                'volume': keyword_metrics.avg_monthly_searches or 0,
                'competition': keyword_metrics.competition.name if keyword_metrics.competition else 'UNKNOWN',
                'competition_index': keyword_metrics.competition_index or 0,
                'cpc_low': round(cpc_low, 2),
                'cpc_high': round(cpc_high, 2)
            })

        return metrics

    except GoogleAdsException as ex:
        logger.error(f"Google Ads API error getting metrics: {ex.failure.errors[0].message}")
        raise


def get_related_keywords(client: GoogleAdsClient, customer_id: str, seed_keywords: list[str]) -> list[dict]:
    """
    Get related keyword suggestions using Keyword Planner.
    Returns top N related keywords with basic metrics.
    """
    if not seed_keywords:
        return []

    keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = customer_id
    request.language = "languageConstants/1000"  # English
    request.geo_target_constants.append("geoTargetConstants/2840")  # United States

    # Use seed keywords
    request.keyword_seed.keywords.extend(seed_keywords[:3])  # Limit seed keywords

    try:
        response = keyword_plan_idea_service.generate_keyword_ideas(request=request)

        related = []
        seen_keywords = set(kw.lower() for kw in seed_keywords)

        for idea in response.results:
            keyword = idea.text

            # Skip if it's one of our seed keywords
            if keyword.lower() in seen_keywords:
                continue

            metrics = idea.keyword_idea_metrics

            related.append({
                'keyword': keyword,
                'volume': metrics.avg_monthly_searches or 0,
                'competition': metrics.competition.name if metrics.competition else 'UNKNOWN'
            })

            if len(related) >= RELATED_KEYWORDS_LIMIT:
                break

        # Sort by volume descending
        related.sort(key=lambda x: x['volume'], reverse=True)
        return related[:RELATED_KEYWORDS_LIMIT]

    except GoogleAdsException as ex:
        logger.error(f"Google Ads API error getting related keywords: {ex.failure.errors[0].message}")
        return []  # Non-fatal, return empty


def update_record_seo(record_id: str, keyword_metrics: list[dict], related_keywords: list[dict]) -> bool:
    """Update Airtable record with SEO data."""
    try:
        fields = {
            'seo_target_keywords': json.dumps(keyword_metrics),
            'seo_long_tail_keywords': json.dumps(related_keywords)
        }

        airtable_request('PATCH', f'/{record_id}', {'fields': fields})
        return True

    except Exception as e:
        logger.error(f"Error updating record {record_id}: {e}")
        return False


def process_record(client: GoogleAdsClient, customer_id: str, record: dict) -> bool:
    """Process a single record: get metrics and update Airtable."""
    record_id = record['id']
    fields = record.get('fields', {})
    keywords_str = fields.get('article_keywords', '')
    title = fields.get('article_title', 'Unknown')[:50]

    logger.info(f"Processing: {title}...")

    # Parse keywords
    keywords = parse_keywords(keywords_str)
    if not keywords:
        logger.warning(f"  No valid keywords found, skipping")
        return False

    logger.info(f"  Found {len(keywords)} keywords: {keywords[:3]}...")

    # Get metrics for keywords
    try:
        metrics = get_keyword_metrics(client, customer_id, keywords)
        logger.info(f"  Got metrics for {len(metrics)} keywords")
    except Exception as e:
        logger.error(f"  Failed to get metrics: {e}")
        return False

    # Get related keyword suggestions
    related = get_related_keywords(client, customer_id, keywords)
    logger.info(f"  Got {len(related)} related keywords")

    # Update Airtable
    success = update_record_seo(record_id, metrics, related)
    if success:
        logger.info(f"  Successfully updated SEO data")

    return success


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting SEO enrichment run")

    try:
        # Get records needing enrichment
        records = get_records_needing_enrichment()

        if not records:
            logger.info("No records need SEO enrichment")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No records to process', 'processed': 0})
            }

        # Initialize Google Ads client
        client = get_google_ads_client()
        creds = get_secret('chipi-rss-pipeline/google-ads')
        # Use manager account for Keyword Planner (tool service, not customer-specific)
        customer_id = creds['login_customer_id']

        # Process each record
        success_count = 0
        fail_count = 0

        for record in records:
            try:
                if process_record(client, customer_id, record):
                    success_count += 1
                else:
                    fail_count += 1
            except GoogleAdsException as ex:
                # Check for quota errors
                for error in ex.failure.errors:
                    if 'RESOURCE_EXHAUSTED' in str(error.error_code):
                        logger.warning("API quota exhausted, stopping run")
                        break
                fail_count += 1
            except Exception as e:
                logger.error(f"Error processing record: {e}")
                fail_count += 1

        summary = {
            'processed': success_count,
            'failed': fail_count,
            'total_found': len(records)
        }
        logger.info(f"SEO enrichment complete: {summary}")

        return {
            'statusCode': 200,
            'body': json.dumps(summary)
        }

    except Exception as e:
        logger.error(f"Fatal error in SEO enricher: {e}")
        raise
