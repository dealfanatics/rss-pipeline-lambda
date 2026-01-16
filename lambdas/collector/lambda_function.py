"""
Chipi RSS Collector Lambda

Fetches RSS feeds from Airtable, filters for relevance using Bedrock,
and queues qualifying articles for processing.

Triggered by: EventBridge (hourly)
Output: SQS messages to chipi-rss-articles queue
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import boto3
import feedparser
import requests

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
secrets_client = boto3.client('secretsmanager', region_name='us-east-1')
sqs_client = boto3.client('sqs', region_name='us-east-1')
bedrock_client = boto3.client('bedrock-runtime', region_name='us-east-1')

# Constants
AIRTABLE_BASE_ID = os.environ.get('AIRTABLE_BASE_ID', 'appE1zQXzgAky8OMJ')
RSS_SOURCES_TABLE = os.environ.get('RSS_SOURCES_TABLE', 'tblHitPKDY321NQ6e')
ARTICLES_TABLE = os.environ.get('ARTICLES_TABLE', 'tblPCyNoy3oK3ao0h')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/221932176915/chipi-rss-articles')
RELEVANCE_THRESHOLD = int(os.environ.get('RELEVANCE_THRESHOLD', '40'))
PRIORITY_THRESHOLD = 70  # Articles scoring >= this are priority extraction targets
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'openai.gpt-oss-120b-1:0')

# Cache for secrets
_secrets_cache = {}


def get_secret(secret_name: str) -> dict:
    """Retrieve secret from Secrets Manager with caching."""
    if secret_name not in _secrets_cache:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        _secrets_cache[secret_name] = json.loads(response['SecretString'])
    return _secrets_cache[secret_name]


def airtable_request(method: str, table_id: str, endpoint: str = '', data: dict = None) -> dict:
    """Make authenticated request to Airtable API."""
    pat = get_secret('chipi-rss-pipeline/airtable')['pat']
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{table_id}{endpoint}"
    headers = {
        'Authorization': f'Bearer {pat}',
        'Content-Type': 'application/json'
    }
    
    response = requests.request(method, url, headers=headers, json=data, timeout=30)
    response.raise_for_status()
    return response.json() if response.text else {}


def get_active_feeds() -> list[dict]:
    """Fetch all active RSS feed sources from Airtable."""
    records = []
    offset = None
    
    while True:
        params = '?filterByFormula={active}=TRUE()'
        if offset:
            params += f'&offset={offset}'
        
        response = airtable_request('GET', RSS_SOURCES_TABLE, params)
        records.extend(response.get('records', []))
        
        offset = response.get('offset')
        if not offset:
            break
    
    logger.info(f"Found {len(records)} active feeds")
    return records


def get_existing_urls() -> set[str]:
    """Fetch all existing article URLs from Airtable for deduplication."""
    urls = set()
    offset = None
    
    while True:
        params = '?fields[]=original_url'
        if offset:
            params += f'&offset={offset}'
        
        response = airtable_request('GET', ARTICLES_TABLE, params)
        
        for record in response.get('records', []):
            url = record.get('fields', {}).get('original_url')
            if url:
                urls.add(url)
        
        offset = response.get('offset')
        if not offset:
            break
    
    logger.info(f"Found {len(urls)} existing URLs for deduplication")
    return urls


def parse_feed(feed_url: str) -> list[dict]:
    """Parse RSS feed and extract items."""
    try:
        feed = feedparser.parse(feed_url)
        items = []
        
        for entry in feed.entries:
            item = {
                'title': entry.get('title', ''),
                'link': entry.get('link', ''),
                'description': entry.get('summary', entry.get('description', '')),
                'published': entry.get('published', ''),
                'source': feed.feed.get('title', urlparse(feed_url).netloc)
            }
            if item['link']:
                items.append(item)
        
        return items
    except Exception as e:
        logger.error(f"Error parsing feed {feed_url}: {e}")
        return []


def batch_relevance_filter(items: list[dict]) -> list[dict]:
    """
    Filter items for relevance using Bedrock.
    Returns only items scoring >= RELEVANCE_THRESHOLD.
    """
    if not items:
        return []

    # Prepare batch for scoring
    items_text = "\n".join([
        f"{i+1}. {item['title'][:100]} - {item['description'][:200]}"
        for i, item in enumerate(items[:50])  # Cap at 50 per batch
    ])

    prompt = f"""Score each article's value for Chipi.ai marketing content on a scale of 0-100 using this rubric:

SCORING RUBRIC (0-100):

- Emotional Resonance (0-30): Can this content trigger fear/greed/envy/pride/hope responses?

  SCORE FOR: Both explicit mentions AND inference potential

  EXPLICIT (article directly states emotions):
  • "Parents worry about..."
  • "Concerns are growing..."
  • "Experts warn..."

  INFERENCE (facts that manufacture emotion):
  • "AI now performs tasks that required college degrees" → FEAR: No jobs left for my kids
  • "AI professionals earning 40% premiums" → GREED: My kid needs these skills
  • "Tech leaders enrolling kids in AI camps" → ENVY: Elite already acting
  • "Most schools lack AI curriculum" → PRIDE: I'll give my child advantages schools don't
  • "AI democratizing access to knowledge" → HOPE: Any child can thrive

  Scoring:
  • 25-30: Multiple strong emotional angles (explicit OR inferable), universally compelling
  • 18-24: 2-3 emotional angles present or strongly inferable
  • 10-17: 1-2 moderate emotional angles
  • 5-9: Weak emotional potential
  • 0-4: No emotional angles, purely technical/academic

  HIGH-VALUE EXAMPLES without "parent" mentions:
  ✅ "AI replaced 30% of entry-level analysts at major firms" (FEAR inference: 25-30 points)
  ✅ "Fortune 500 companies requiring AI literacy for new hires" (URGENCY inference: 20-25 points)
  ✅ "AI skills shortage: 100K unfilled positions" (OPPORTUNITY inference: 20-25 points)

- Universality & Actionability (0-25): Can insights be generalized beyond specific programs/locations?
  • 20-25: Universal truths applicable anywhere + clear action triggers
  • 14-19: Broadly generalizable with moderate action potential
  • 8-13: Somewhat specific but extractable patterns exist
  • 4-7: Mostly local/program-specific but some universal elements
  • 0-3: Purely local/non-actionable

- Authority & Evidence (0-20): Does it contain quotes, data, expert opinions, or research?
  • 16-20: Multiple credible sources + hard statistics/research
  • 11-15: Good sources with some quantitative data
  • 6-10: Some authority signals (expert quotes OR data)
  • 3-5: Minimal evidence (opinions with weak attribution)
  • 0-2: No sources or evidence

- Content Depth (0-15): Is there sufficient extractable content?
  • 12-15: Full article, rich detail, multiple angles
  • 8-11: Full article, moderate depth
  • 4-7: Partial content or shallow coverage
  • 1-3: Minimal content or heavily paywalled
  • 0: No accessible content

- Strategic Alignment (0-10): Relevance to AI/technology education and future preparedness
  • 9-10: Core AI education for children/students
  • 7-8: Adjacent and highly relevant (STEM education, coding, future of work affecting youth, educational technology)
  • 5-6: Tangentially relevant (general tech trends, workforce shifts, parenting challenges)
  • 3-4: Distant connection (general education, technology news)
  • 0-2: Off-topic

MINIMUM THRESHOLD: Articles scoring <40 are discarded. Articles ≥70 are priority extraction targets.

CRITICAL INSTRUCTION: When scoring Emotional Resonance, ask yourself:
"Could I write 3+ compelling marketing angles from this content, even if parents/children aren't mentioned?"
If YES → score 20+
If MAYBE → score 10-19
If NO → score <10

Articles:
{items_text}

Respond with ONLY a JSON object:
{{
  "scores": [85, 42, 73, ...],
  "reasoning": [
    "Article 1: Emotional(28/30-multiple inference angles: job displacement fear+wage premium greed), Universal(22/25-applicable everywhere), Authority(18/20-hard data+expert quotes), Depth(14/15-full article), Alignment(9/10-AI workforce)",
    "Article 2: Emotional(8/30-weak angles), Universal(12/25-somewhat specific), Authority(6/20-minimal sources), Depth(11/15-decent content), Alignment(5/10-tangential)",
    ...
  ]
}}"""

    try:
        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType='application/json',
            accept='application/json',
            body=json.dumps({
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 4000,
                'temperature': 0
            })
        )

        raw_body = response['body'].read()
        logger.info(f"Bedrock raw response length: {len(raw_body)} bytes")

        result = json.loads(raw_body)
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '{}')
        logger.info(f"Bedrock content (last 500 chars): {content[-500:]}")

        # Parse response - handle reasoning tags and markdown formatting
        content = content.strip()

        # Remove <reasoning>...</reasoning> tags if present
        if '<reasoning>' in content:
            import re
            content = re.sub(r'<reasoning>.*?</reasoning>', '', content, flags=re.DOTALL).strip()

        # Handle markdown code blocks
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()

        # Extract JSON object from content
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group(0)

        logger.info(f"Parsed content for JSON (first 300 chars): {content[:300]}")
        parsed = json.loads(content)

        scores = parsed.get('scores', [])
        reasoning_list = parsed.get('reasoning', [])

        # Filter items by score
        filtered = []
        for i, item in enumerate(items[:len(scores)]):
            if i < len(scores) and scores[i] >= RELEVANCE_THRESHOLD:
                item['relevance_score'] = scores[i]
                item['is_priority'] = scores[i] >= PRIORITY_THRESHOLD
                # Strip "Article N:" prefix from reasoning
                reasoning = reasoning_list[i] if i < len(reasoning_list) else ''
                if reasoning.startswith('Article ') and ': ' in reasoning:
                    reasoning = reasoning.split(': ', 1)[1]
                item['scoring_reasoning'] = reasoning
                filtered.append(item)

        logger.info(f"Relevance filter: {len(filtered)}/{len(items)} items passed (threshold={RELEVANCE_THRESHOLD}, priority={PRIORITY_THRESHOLD})")
        return filtered

    except Exception as e:
        logger.error(f"Relevance filtering error: {e}")
        # On error, pass all items through (fail open)
        return items


def is_google_news_url(url: str) -> bool:
    """Check if URL is a Google News redirect."""
    return 'news.google.com' in url


def queue_article(item: dict, feed_name: str) -> bool:
    """Send article to SQS queue for processing."""
    try:
        message = {
            'url': item['link'],
            'title': item['title'],
            'description': item['description'],
            'source': item['source'],
            'feed_name': feed_name,
            'is_google_news': is_google_news_url(item['link']),
            'relevance_score': item.get('relevance_score', 0),
            'is_priority': item.get('is_priority', False),
            'scoring_reasoning': item.get('scoring_reasoning', ''),
            'queued_at': datetime.now(timezone.utc).isoformat()
        }

        # Build SQS params - only include MessageGroupId for FIFO queues
        sqs_params = {
            'QueueUrl': SQS_QUEUE_URL,
            'MessageBody': json.dumps(message)
        }
        if '.fifo' in SQS_QUEUE_URL:
            sqs_params['MessageGroupId'] = feed_name

        sqs_client.send_message(**sqs_params)
        return True
    except Exception as e:
        logger.error(f"Error queuing article {item['link']}: {e}")
        return False


def update_feed_status(record_id: str, items_processed: int, error: Optional[str] = None):
    """Update feed record in Airtable with processing status."""
    fields = {
        'lastFetched': datetime.now(timezone.utc).isoformat(),
    }
    
    if error:
        fields['lastError'] = error[:10000]  # Airtable field limit
    else:
        fields['lastError'] = ''
    
    # Increment itemsProcessed
    try:
        current = airtable_request('GET', RSS_SOURCES_TABLE, f'/{record_id}')
        current_count = current.get('fields', {}).get('itemsProcessed', 0) or 0
        fields['itemsProcessed'] = current_count + items_processed
    except:
        fields['itemsProcessed'] = items_processed
    
    airtable_request('PATCH', RSS_SOURCES_TABLE, f'/{record_id}', {'fields': fields})


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting RSS collection run")
    
    try:
        # Get active feeds
        feeds = get_active_feeds()
        if not feeds:
            logger.info("No active feeds found")
            return {'statusCode': 200, 'body': 'No active feeds'}
        
        # Get existing URLs for deduplication
        existing_urls = get_existing_urls()
        
        total_queued = 0
        total_skipped = 0
        
        for feed_record in feeds:
            feed_id = feed_record['id']
            fields = feed_record.get('fields', {})
            feed_name = fields.get('feedName', 'Unknown')
            feed_url = fields.get('feedUrl')
            
            if not feed_url:
                logger.warning(f"Feed {feed_name} has no URL, skipping")
                update_feed_status(feed_id, 0, "No feed URL configured")
                continue
            
            logger.info(f"Processing feed: {feed_name}")
            
            try:
                # Parse feed
                items = parse_feed(feed_url)
                logger.info(f"  Found {len(items)} items in feed")
                
                # Deduplicate
                new_items = [item for item in items if item['link'] not in existing_urls]
                skipped = len(items) - len(new_items)
                total_skipped += skipped
                logger.info(f"  {len(new_items)} new items after deduplication ({skipped} skipped)")
                
                if not new_items:
                    update_feed_status(feed_id, 0)
                    continue
                
                # Relevance filter
                relevant_items = batch_relevance_filter(new_items)
                logger.info(f"  {len(relevant_items)} items passed relevance filter")
                
                # Queue for processing
                queued = 0
                for item in relevant_items:
                    if queue_article(item, feed_name):
                        queued += 1
                        existing_urls.add(item['link'])  # Prevent re-queuing in same run
                
                total_queued += queued
                update_feed_status(feed_id, queued)
                logger.info(f"  Queued {queued} articles from {feed_name}")
                
            except Exception as e:
                logger.error(f"Error processing feed {feed_name}: {e}")
                update_feed_status(feed_id, 0, str(e))
        
        summary = {
            'feeds_processed': len(feeds),
            'articles_queued': total_queued,
            'duplicates_skipped': total_skipped
        }
        logger.info(f"Collection complete: {summary}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(summary)
        }
        
    except Exception as e:
        logger.error(f"Fatal error in collector: {e}")
        raise
