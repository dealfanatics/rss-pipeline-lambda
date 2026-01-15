"""
Chipi RSS Processor Lambda

Processes articles from SQS queue:
1. Decodes Google News URLs if needed
2. Fetches article content via proxy
3. Extracts structured data using Bedrock
4. Writes to Airtable articleExtractLibrary

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
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'openai.gpt-oss-120b-1:0')

# Cache
_secrets_cache = {}

# Browser-like headers
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

# Extraction prompt (from your n8n workflow)
EXTRACTION_PROMPT = """You are an AI analyst tasked with extracting content from news articles about AI or technology for use in marketing educational AI products to parents.

Your goal is to identify universal themes and parental motivators that transcend specific locations or circumstances. Focus on extracting:

1. Universal parental concerns about AI/technology
2. Emotional triggers (fear, hope, pride, etc.) that could motivate parents to seek AI education
3. Key themes that could be used in marketing content

IMPORTANT RULES:
- Generalize location-specific details (e.g., "a school district" instead of "Springfield School District")
- Extract patterns that apply universally to parents, not just to specific demographics
- Focus on emotional resonance and actionable marketing angles
- If the article is paywalled or incomplete, extract what you can and note the limitation

Analyze the following article and provide a JSON response with this exact structure:
{
    "article_status": "complete" | "partial" | "paywalled" | "error",
    "article_author": "Author name or null",
    "article_title": "Article title",
    "publication_date": "YYYY-MM-DD or null",
    "publisher_name": "Publisher name",
    "article_summary": "2-3 sentence summary focusing on parental relevance",
    "key_themes": "theme1 | theme2 | theme3",
    "emotion_ideas_fear": "Fear angle for parents or empty string",
    "emotion_ideas_greed": "Opportunity/advantage angle or empty string", 
    "emotion_ideas_envy": "Comparison/competition angle or empty string",
    "emotion_ideas_pride": "Achievement/validation angle or empty string",
    "emotion_ideas_hope": "Positive future angle or empty string",
    "key_quotes": "Notable quote 1 | Notable quote 2",
    "article_keywords": "keyword1, keyword2, keyword3"
}

ARTICLE CONTENT:
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
    Reverse-engineered from n8n workflow.
    """
    try:
        # Extract article code from URL
        # Format: https://news.google.com/articles/CBMi...
        parsed = urlparse(google_url)
        path_parts = parsed.path.split('/')
        
        if 'articles' not in path_parts:
            logger.warning(f"Not a Google News article URL: {google_url}")
            return google_url
        
        article_idx = path_parts.index('articles')
        if article_idx + 1 >= len(path_parts):
            return google_url
            
        article_code = path_parts[article_idx + 1]
        
        # Fetch the Google News page to get signature and timestamp
        response = requests.get(google_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the element with signature data
        # Looking for data-n-a-sg and data-n-a-ts attributes
        article_element = soup.find(attrs={'data-n-a-sg': True})
        
        if not article_element:
            # Try alternate method - look for c-wiz with data attributes
            article_element = soup.find('c-wiz', attrs={'data-n-a-sg': True})
        
        if not article_element:
            logger.warning(f"Could not find signature in Google News page, trying direct fetch")
            # Fallback: try to find actual URL in page
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.startswith('http') and 'news.google.com' not in href:
                    return href
            return google_url
        
        signature = article_element.get('data-n-a-sg', '')
        timestamp = article_element.get('data-n-a-ts', '')
        
        if not signature or not timestamp:
            logger.warning("Missing signature or timestamp")
            return google_url
        
        # POST to Google's decoding endpoint
        decode_url = "https://news.google.com/rss/articles/" + article_code
        decode_response = requests.get(decode_url, headers=HEADERS, timeout=15, allow_redirects=True)
        
        # The final URL after redirects is the actual article
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
        
        return response.text, 'success'
        
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


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    
    # Remove script and style elements
    for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        element.decompose()
    
    # Try to find article body
    article = soup.find('article') or soup.find(class_=re.compile(r'article|post|content|story'))
    
    if article:
        text = article.get_text(separator='\n', strip=True)
    else:
        # Fallback to body
        body = soup.find('body')
        text = body.get_text(separator='\n', strip=True) if body else soup.get_text(separator='\n', strip=True)
    
    # Clean up whitespace
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)[:15000]  # Cap at ~15k chars for context window


def extract_with_bedrock(article_text: str, url: str, source: str) -> dict:
    """Extract structured data from article using Bedrock."""
    prompt = EXTRACTION_PROMPT + f"\nURL: {url}\nSource: {source}\n\n{article_text}"
    
    try:
        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType='application/json',
            accept='application/json',
            body=json.dumps({
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 2000,
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
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, flags=re.DOTALL)
        if json_match:
            content = json_match.group(0)

        logger.info(f"Parsed JSON content (first 200 chars): {content[:200]}")
        extracted = json.loads(content)
        return extracted
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in extraction: {e}")
        return {'article_status': 'error', 'error': 'Invalid JSON response'}
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        return {'article_status': 'error', 'error': str(e)}


def write_to_airtable(extracted: dict, original_url: str, feed_name: str, relevance_score: int, is_priority: bool = False, scoring_reasoning: str = '') -> bool:
    """Write extracted article data to Airtable."""
    try:
        fields = {
            'original_url': original_url,
            'feed_source': feed_name,
            'relevance_score': relevance_score,
            'is_priority': is_priority,
            'scoring_reasoning': scoring_reasoning,
            'processed_at': datetime.now(timezone.utc).isoformat(),
            'article_status': extracted.get('article_status', 'unknown'),
            'article_author': extracted.get('article_author') or '',
            'article_title': extracted.get('article_title') or '',
            'publication_date': extracted.get('publication_date') or '',
            'publisher_name': extracted.get('publisher_name') or '',
            'article_summary': extracted.get('article_summary') or '',
            'key_themes': extracted.get('key_themes') or '',
            'emotion_ideas_fear': extracted.get('emotion_ideas_fear') or '',
            'emotion_ideas_greed': extracted.get('emotion_ideas_greed') or '',
            'emotion_ideas_envy': extracted.get('emotion_ideas_envy') or '',
            'emotion_ideas_pride': extracted.get('emotion_ideas_pride') or '',
            'emotion_ideas_hope': extracted.get('emotion_ideas_hope') or '',
            'key_quotes': extracted.get('key_quotes') or '',
            'article_keywords': extracted.get('article_keywords') or ''
        }
        
        # Filter out empty strings for cleaner records
        fields = {k: v for k, v in fields.items() if v != '' or k in ['original_url', 'article_status']}
        
        airtable_request('POST', ARTICLES_TABLE, '', {'fields': fields})
        return True
        
    except Exception as e:
        logger.error(f"Error writing to Airtable: {e}")
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
        # Still write a record so we don't retry endlessly
        extracted = {
            'article_status': fetch_status,
            'article_title': message.get('title', ''),
            'publisher_name': source
        }
        write_to_airtable(extracted, url, feed_name, relevance_score, is_priority, scoring_reasoning)
        return {'status': fetch_status, 'url': url}
    
    # Extract text from HTML
    article_text = extract_text_from_html(html)
    logger.info(f"  Extracted {len(article_text)} chars of text")
    
    if len(article_text) < 100:
        logger.warning("  Article text too short, may be paywalled")
        extracted = {
            'article_status': 'partial',
            'article_title': message.get('title', ''),
            'publisher_name': source
        }
        write_to_airtable(extracted, url, feed_name, relevance_score, is_priority, scoring_reasoning)
        return {'status': 'partial', 'url': url}
    
    # Extract with AI
    logger.info("  Running AI extraction...")
    extracted = extract_with_bedrock(article_text, actual_url, source)
    
    # Write to Airtable
    success = write_to_airtable(extracted, url, feed_name, relevance_score, is_priority, scoring_reasoning)
    
    return {
        'status': 'success' if success else 'write_failed',
        'url': url,
        'article_status': extracted.get('article_status', 'unknown')
    }


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
            
            if result['status'] not in ['success', 'paywalled', 'partial']:
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
