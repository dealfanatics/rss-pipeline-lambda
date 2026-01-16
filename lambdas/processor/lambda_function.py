"""
Chipi RSS Processor Lambda v2

Processes articles from SQS queue:
1. Decodes Google News URLs if needed
2. Fetches article content via proxy
3. Extracts structured data using Bedrock with Strategic Signal framework
4. Writes successes to Airtable articleExtractLibrary
5. Writes failures to Airtable failedArticles

Triggered by: SQS (chipi-rss-articles queue)
"""

import json
import os
import re
import logging
import random
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, parse_qs

import boto3
import requests
from bs4 import BeautifulSoup

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
secrets_client = boto3.client('secretsmanager', region_name='us-east-1')
bedrock_client = boto3.client('bedrock-runtime', region_name='us-east-1')

# Constants
AIRTABLE_BASE_ID = os.environ.get('AIRTABLE_BASE_ID', 'appE1zQXzgAky8OMJ')
ARTICLES_TABLE = os.environ.get('ARTICLES_TABLE', 'tblPCyNoy3oK3ao0h')
FAILED_TABLE = os.environ.get('FAILED_TABLE', 'tbl4J5DyNqzdJwcve')
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'openai.gpt-oss-120b-1:0')
ARTICLE_TEXT_LIMIT = 90000

# Cache
_secrets_cache = {}

# Browser-like headers (no Accept-Encoding to avoid proxy compression issues)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

# New extraction prompt with Strategic Signal framework
EXTRACTION_PROMPT = """Extract marketing intelligence from this article for Chipi.ai (AI education platform for children).

Article scored: {score}/100
Strong dimensions: {scoring_reasoning}

CONTEXT: Extract universal parental motivators that work across geographies, programs, and demographics. Transform specific examples into generalizable patterns.

=== EXTRACTION SCHEMA ===

## STEP 1: Core Metadata
- article_author: (use publisher_name if unavailable)
- publication_date: MM/DD/YYYY format
- publisher_name:
- article_title:

## STEP 2: Strategic Signal Extraction (article_summary)

Extract **UNIVERSAL STRATEGIC SIGNALS** present in the article and generalize them for parental decision-making related to children's education, enrichment, future skills and general readiness.

DO NOT force relevance if signals are weak or absent.
DO NOT rely on program names, locations, institutions, or dates.

### Strategic Signal Types
(Activate only if supported by evidence in the article.)

- **Capability Shift**: New abilities becoming possible or easier to acquire
- **Baseline Expectation Shift**: Skills or knowledge moving from optional → assumed
- **Access & Democratization**: Barriers falling; advantages spreading beyond elite groups
- **Acceleration / Compression**: Time, cost, or effort required collapsing materially
- **Risk Exposure (Inaction or Passive Use)**: Downside of not adapting or remaining a passive consumer
- **Second-Order Effects**: Downstream consequences beyond the obvious first impact
- **Control & Agency**: Shift in who controls outcomes (institutions → families / individuals)

### Category-Aware Interpretation Rails

**Business / Economics / Work**
Signals: Labor market shifts, wage premiums/penalties, productivity expectations, skill obsolescence
Translate: What skills will soon be economically assumed; what creates durable advantage

**Technology / AI / Innovation**
Signals: Tool capability improvements, automation thresholds, cost/performance curves
Translate: What children can now do earlier; what understanding replaces rote usage

**Education / Learning / Cognitive Science**
Signals: Learning outcome research, cognitive development findings, pedagogical gaps
Translate: How learning itself is changing; why early exposure compounds

**Society / Culture / Inequality**
Signals: Stratification effects, early vs late adopter gaps, access disparities
Translate: Who moves ahead and why; what happens when this becomes normal

**Parenting / Lifestyle / Family**
Signals: Screen-time debates, skill anxiety, agency vs outsourcing decisions
Translate: What parents should actively guide; how parents retain control

**Science / Research / Data**
Signals: Empirical findings, longitudinal trends, measured deltas
Translate: What evidence supports early action; which effects persist

### Output for article_summary
Return a JSON object with:
- "signals": array of signal types identified (from the 7 types above)
- "summary": 2-4 paragraph strategic synthesis

## STEP 3: Marketable Themes (key_themes)
JSON array:
[
  {{
    "theme": "Universal pattern from article",
    "marketing_hook": "How this drives parental action",
    "evidence": "Supporting data/quote if available"
  }}
]

## STEP 4: Emotional Angles

fear_angles: [{{"trigger": "...", "intensity": "high|medium|low", "quote_support": "..."}}]
greed_angles: [{{"benefit": "...", "quantification": "...", "timeframe": "..."}}]
envy_angles: [{{"social_proof": "...", "consequence": "...", "fomo_trigger": "..."}}]
pride_angles: [{{"identity": "...", "differentiation": "..."}}]
hope_angles: [{{"transformation": "...", "accessibility": "...", "empowerment": "..."}}]

## STEP 5: Authority Arsenal

credible_sources: [{{name, title, affiliation}}]
data_points: [{{statistic, source, context}}]
research_findings: [{{finding, institution, implications}}]
key_quotes: [{{quote, speaker, credentials, relevance_score (0-10)}}]

## STEP 6: Keywords

article_keywords: 3-7 keywords, pipe-delimited string
- Extract based on frequency + title presence + semantic centrality
- Focus on parent search intent alignment

=== GENERALIZATION RULES ===
1. GEOGRAPHIC: "Nebraska" → "forward-thinking districts"
2. INSTITUTIONAL: "UNO students" → "students in university AI programs"
3. TEMPORAL: "Spring 2025" → "upcoming enrollment periods"
4. PROGRAMMATIC: "This certification" → "AI credential programs"

=== QUALITY CHECK ===
Before output, verify:
- Insight holds if all proper nouns removed
- A parent in any U.S. city would find it relevant
- Signals reflect durable forces, not one-time events
- Summary can be reused in marketing copy without revision

=== OUTPUT ===
Return ONLY valid JSON with this structure:
{{
  "article_author": "...",
  "article_title": "...",
  "publication_date": "MM/DD/YYYY",
  "publisher_name": "...",
  "article_summary": {{"signals": [...], "summary": "..."}},
  "key_themes": [...],
  "fear_angles": [...],
  "greed_angles": [...],
  "envy_angles": [...],
  "pride_angles": [...],
  "hope_angles": [...],
  "credible_sources": [...],
  "data_points": [...],
  "research_findings": [...],
  "key_quotes": [...],
  "article_keywords": "keyword1 | keyword2 | keyword3"
}}

ARTICLE TEXT:
"""


def get_secret(secret_name: str) -> dict:
    """Retrieve secret from Secrets Manager with caching."""
    if secret_name not in _secrets_cache:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        _secrets_cache[secret_name] = json.loads(response['SecretString'])
    return _secrets_cache[secret_name]


def get_random_proxy() -> dict:
    """Get a random proxy from Secrets Manager."""
    proxies = get_secret('chipi-rss-pipeline/proxies')['proxies']
    proxy = random.choice(proxies)
    proxy_url = f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
    return {
        'http': proxy_url,
        'https': proxy_url
    }


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


def decode_google_news_url(google_url: str) -> Optional[str]:
    """
    Decode Google News redirect URL to get actual article URL.
    """
    try:
        parsed = urlparse(google_url)
        path_parts = parsed.path.split('/')

        if 'articles' not in path_parts:
            logger.warning(f"Not a Google News article URL: {google_url}")
            return google_url

        article_idx = path_parts.index('articles')
        if article_idx + 1 >= len(path_parts):
            return google_url

        article_code = path_parts[article_idx + 1]

        response = requests.get(google_url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        article_element = soup.find(attrs={'data-n-a-sg': True})

        if not article_element:
            article_element = soup.find('c-wiz', attrs={'data-n-a-sg': True})

        if not article_element:
            logger.warning(f"Could not find signature in Google News page, trying direct fetch")
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.startswith('http') and 'news.google.com' not in href:
                    return href
            return google_url

        decode_url = "https://news.google.com/rss/articles/" + article_code
        decode_response = requests.get(decode_url, headers=HEADERS, timeout=15, allow_redirects=True)

        if decode_response.url and 'news.google.com' not in decode_response.url:
            return decode_response.url

        return google_url

    except Exception as e:
        logger.error(f"Error decoding Google News URL {google_url}: {e}")
        return google_url


def fetch_article_content(url: str, use_proxy: bool = True) -> tuple[str, str]:
    """
    Fetch article content with proxy rotation.
    Returns (html_content, status).
    """
    try:
        proxies = get_random_proxy() if use_proxy else None

        response = requests.get(
            url,
            headers=HEADERS,
            proxies=proxies,
            timeout=30,
            allow_redirects=True
        )
        response.raise_for_status()

        # Explicitly handle encoding - try apparent_encoding if encoding detection fails
        if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
            response.encoding = response.apparent_encoding or 'utf-8'

        html = response.text

        # Check content-type header for HTML
        content_type = response.headers.get('content-type', '').lower()
        is_html_content = 'text/html' in content_type

        # Validate we got something that looks like HTML
        has_html_markers = len(html) > 500 and (
            '<' in html[:1000] or is_html_content
        )

        if not html or not has_html_markers:
            logger.warning(f"Response doesn't appear to be HTML (content-type: {content_type}, length: {len(html)})")
            return '', 'not_html'

        return html, 'success'

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            return '', 'paywalled'
        elif e.response.status_code == 404:
            return '', 'not_found'
        else:
            return '', f'http_error_{e.response.status_code}'
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return '', 'fetch_error'


def sanitize_text(text: str) -> str:
    """Remove non-printable characters and ensure valid UTF-8."""
    # Remove control characters except newline and tab
    sanitized = ''.join(char for char in text if char.isprintable() or char in '\n\t')
    # Normalize whitespace
    sanitized = re.sub(r'[ \t]+', ' ', sanitized)
    sanitized = re.sub(r'\n{3,}', '\n\n', sanitized)
    return sanitized.strip()


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML, capped at 90K chars."""
    soup = BeautifulSoup(html, 'html.parser')

    # Remove script and style elements
    for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        element.decompose()

    # Try to find article body
    article = soup.find('article') or soup.find(class_=re.compile(r'article|post|content|story'))

    if article:
        text = article.get_text(separator='\n', strip=True)
    else:
        body = soup.find('body')
        text = body.get_text(separator='\n', strip=True) if body else soup.get_text(separator='\n', strip=True)

    # Clean up whitespace
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    text = '\n'.join(lines)

    # Sanitize and validate text
    text = sanitize_text(text)

    # Validate text is mostly readable (at least 80% printable ASCII)
    printable_ratio = sum(1 for c in text[:1000] if c.isascii() and c.isprintable() or c in '\n\t') / max(len(text[:1000]), 1)
    if printable_ratio < 0.7:
        logger.warning(f"Text appears corrupted (printable ratio: {printable_ratio:.2f})")
        return ''

    return text[:ARTICLE_TEXT_LIMIT]


def extract_with_bedrock(article_text: str, url: str, source: str, score: int, scoring_reasoning: str) -> dict:
    """Extract structured data from article using Bedrock."""
    # Build prompt with score and reasoning injected
    prompt = EXTRACTION_PROMPT.format(
        score=score,
        scoring_reasoning=scoring_reasoning
    ) + f"\nURL: {url}\nSource: {source}\n\n{article_text}"

    try:
        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType='application/json',
            accept='application/json',
            body=json.dumps({
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 4000,
                'temperature': 0.1
            })
        )

        result = json.loads(response['body'].read())
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '{}')

        # Parse JSON from response - handle reasoning tags and markdown
        content = content.strip()

        # Remove <reasoning>...</reasoning> tags if present
        if '<reasoning>' in content:
            content = re.sub(r'<reasoning>.*?</reasoning>', '', content, flags=re.DOTALL).strip()

        # Handle markdown code blocks
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0]

        # Extract JSON object from remaining content
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group(0)

        logger.info(f"Parsed JSON content (first 300 chars): {content[:300]}")
        extracted = json.loads(content)
        return extracted

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in extraction: {e}")
        raise Exception(f"JSON decode error: {e}")
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        raise


def write_to_airtable(extracted: dict, original_url: str, feed_name: str,
                      relevance_score: int, is_priority: bool, scoring_reasoning: str,
                      article_text: str) -> bool:
    """Write extracted article data to Airtable articleExtractLibrary."""
    try:
        # Handle article_summary - could be JSON object or string
        article_summary = extracted.get('article_summary', '')
        if isinstance(article_summary, dict):
            article_summary = json.dumps(article_summary)

        # Serialize JSON arrays
        def to_json_str(val):
            if isinstance(val, (list, dict)):
                return json.dumps(val)
            return val or ''

        fields = {
            'original_url': original_url,
            'feed_source': feed_name,
            'relevance_score': relevance_score,
            'is_priority': is_priority,
            'scoring_reasoning': scoring_reasoning,
            'processed_at': datetime.now(timezone.utc).isoformat(),
            'article_text': article_text[:ARTICLE_TEXT_LIMIT],
            'article_author': extracted.get('article_author') or '',
            'article_title': extracted.get('article_title') or '',
            'publication_date': extracted.get('publication_date') or '',
            'publisher_name': extracted.get('publisher_name') or '',
            'article_summary': article_summary,
            'key_themes': to_json_str(extracted.get('key_themes')),
            'emotion_ideas_fear': to_json_str(extracted.get('fear_angles')),
            'emotion_ideas_greed': to_json_str(extracted.get('greed_angles')),
            'emotion_ideas_envy': to_json_str(extracted.get('envy_angles')),
            'emotion_ideas_pride': to_json_str(extracted.get('pride_angles')),
            'emotion_ideas_hope': to_json_str(extracted.get('hope_angles')),
            'key_quotes': to_json_str(extracted.get('key_quotes')),
            'credible_sources': to_json_str(extracted.get('credible_sources')),
            'data_points': to_json_str(extracted.get('data_points')),
            'research_findings': to_json_str(extracted.get('research_findings')),
            'article_keywords': extracted.get('article_keywords') or ''
        }

        # Filter out empty strings for cleaner records (keep essential fields)
        fields = {k: v for k, v in fields.items() if v != '' or k in ['original_url']}

        airtable_request('POST', ARTICLES_TABLE, '', {'fields': fields})
        return True

    except Exception as e:
        logger.error(f"Error writing to Airtable: {e}")
        raise


def write_to_failed_articles(url: str, feed_source: str, failure_reason: str, error_details: str) -> bool:
    """Write failed article to failedArticles table."""
    try:
        fields = {
            'url': url,
            'feed_source': feed_source,
            'failure_reason': failure_reason,
            'error_details': error_details[:10000],  # Truncate long errors
            'failed_at': datetime.now(timezone.utc).isoformat(),
            'retry_count': 0
        }

        airtable_request('POST', FAILED_TABLE, '', {'fields': fields})
        logger.info(f"  Logged failure to failedArticles: {failure_reason}")
        return True

    except Exception as e:
        logger.error(f"Error writing to failedArticles: {e}")
        return False


def process_article(message: dict) -> dict:
    """Process a single article from the queue."""
    url = message['url']
    feed_name = message.get('feed_name', 'Unknown')
    source = message.get('source', '')
    is_google_news = message.get('is_google_news', False)
    relevance_score = message.get('relevance_score', 0)
    is_priority = message.get('is_priority', False)
    scoring_reasoning = message.get('scoring_reasoning', '')

    logger.info(f"Processing: {url[:80]}...")

    # Decode Google News URL if needed
    actual_url = url
    if is_google_news:
        logger.info("  Decoding Google News URL...")
        actual_url = decode_google_news_url(url) or url
        if actual_url != url:
            logger.info(f"  Decoded to: {actual_url[:80]}...")

    # Fetch content
    html, fetch_status = fetch_article_content(actual_url)

    if not html:
        logger.warning(f"  Fetch failed: {fetch_status}")
        write_to_failed_articles(url, feed_name, 'fetch_error', f"Status: {fetch_status}")
        return {'status': fetch_status, 'url': url}

    # Extract text from HTML
    article_text = extract_text_from_html(html)
    logger.info(f"  Extracted {len(article_text)} chars of text")

    if len(article_text) < 100:
        logger.warning("  Article text too short, may be paywalled")
        write_to_failed_articles(url, feed_name, 'too_short', f"Only {len(article_text)} chars extracted")
        return {'status': 'too_short', 'url': url}

    # Extract with AI
    logger.info("  Running AI extraction...")
    try:
        extracted = extract_with_bedrock(article_text, actual_url, source, relevance_score, scoring_reasoning)
    except Exception as e:
        logger.error(f"  Extraction failed: {e}")
        write_to_failed_articles(url, feed_name, 'extraction_error', str(e))
        return {'status': 'extraction_error', 'url': url}

    # Write to Airtable
    try:
        write_to_airtable(extracted, url, feed_name, relevance_score, is_priority, scoring_reasoning, article_text)
        logger.info("  Successfully wrote to Airtable")
        return {'status': 'success', 'url': url}
    except Exception as e:
        logger.error(f"  Airtable write failed: {e}")
        write_to_failed_articles(url, feed_name, 'airtable_error', str(e))
        return {'status': 'airtable_error', 'url': url}


def lambda_handler(event, context):
    """Main Lambda handler - processes SQS batch."""
    logger.info(f"Processing {len(event.get('Records', []))} messages")

    results = []
    failures = []

    for record in event.get('Records', []):
        try:
            message = json.loads(record['body'])
            result = process_article(message)
            results.append(result)

            # Only retry on transient failures, not on content issues
            if result['status'] in ['fetch_error', 'airtable_error']:
                failures.append(record['messageId'])

        except Exception as e:
            logger.error(f"Error processing message {record['messageId']}: {e}")
            failures.append(record['messageId'])

    logger.info(f"Processed {len(results)} articles, {len(failures)} failures")

    # Return failures for SQS to retry
    if failures:
        return {
            'batchItemFailures': [{'itemIdentifier': msg_id} for msg_id in failures]
        }

    return {'statusCode': 200}
