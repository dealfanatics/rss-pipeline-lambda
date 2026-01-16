# Google Ads API Integration Design Document

**Company:** Chipi.ai (AI Projects for Kids, LLC)
**Application:** Google Ads API Basic Access
**Document Version:** 1.0
**Date:** January 2026

---

## Executive Summary

Chipi.ai is an AI education platform for children that helps parents prepare their kids for an AI-driven future. This document describes our marketing automation system, the role of Google Ads API integration, and our roadmap for automated campaign performance management.

We are requesting Basic Access to the Google Ads API to power our content marketing intelligence system, which uses Keyword Planner data to optimize content creation and distribution strategies.

---

## 1. Business Background

### 1.1 Company Overview

**Chipi.ai** is an educational technology company focused on AI literacy for children ages 6-14. Our mission is to democratize AI education by providing accessible, engaging curriculum that teaches children to be creators with AI rather than passive consumers.

**Key Business Facts:**
- Founded: 2024
- Headquarters: United States
- Target Market: Parents of children ages 6-14
- Business Model: Subscription-based online learning platform
- Google Ads Customer ID: 122-997-6456
- Manager Account ID: 923-604-0286

### 1.2 Marketing Challenge

Parents searching for AI education resources face an overwhelming landscape of options. Our marketing challenge is threefold:

1. **Discovery**: Help parents find relevant, trustworthy information about AI education
2. **Education**: Provide valuable content that addresses parental concerns and motivations
3. **Conversion**: Guide interested parents toward our platform through targeted campaigns

### 1.3 Current Marketing Operations

We currently operate:
- Google Ads campaigns for customer acquisition
- Content marketing via blog articles and social media
- Email marketing for nurturing and retention

Our Google Ads account is actively running campaigns with established conversion tracking and performance history.

---

## 2. System Description: Content Generation Engine

### 2.1 Purpose

Our Content Generation Engine is an automated pipeline that:

1. **Discovers** relevant news and research about AI, education, and parenting
2. **Analyzes** content for marketing potential using AI-powered extraction
3. **Enriches** content with SEO intelligence from Google Keyword Planner
4. **Generates** marketing content optimized for search and paid channels

### 2.2 System Components

#### Component 1: RSS Collector
- Monitors curated RSS feeds for AI/education news
- Filters articles using relevance scoring (0-100 scale)
- Queues qualifying articles for processing

#### Component 2: Article Processor
- Extracts structured marketing intelligence from articles
- Identifies emotional angles (fear, hope, pride, etc.)
- Captures authority signals (quotes, statistics, research)
- Generates strategic summaries for content creation

#### Component 3: SEO Enricher (Google Ads API Integration)
- Retrieves keyword metrics from Google Keyword Planner
- Analyzes search volume and competition for extracted keywords
- Identifies related long-tail keyword opportunities
- Prioritizes content based on search demand

#### Component 4: Content Generator (Planned)
- Creates blog posts, ad copy, and social content
- Optimizes for keywords with proven search demand
- Maintains brand voice and compliance standards

### 2.3 Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CONTENT GENERATION ENGINE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│   │  RSS Feeds   │───▶│  Collector   │───▶│   Message    │                  │
│   │  (External)  │    │   Lambda     │    │    Queue     │                  │
│   └──────────────┘    └──────────────┘    └──────┬───────┘                  │
│                                                   │                          │
│                                                   ▼                          │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│   │   Airtable   │◀───│  Processor   │◀───│   Article    │                  │
│   │   Database   │    │   Lambda     │    │   Fetcher    │                  │
│   └──────┬───────┘    └──────────────┘    └──────────────┘                  │
│          │                                                                   │
│          │ Articles with keywords                                            │
│          ▼                                                                   │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│   │     SEO      │───▶│   Google     │───▶│   Enriched   │                  │
│   │   Enricher   │    │  Keyword     │    │   Content    │                  │
│   │   Lambda     │    │  Planner API │    │   Database   │                  │
│   └──────────────┘    └──────────────┘    └──────┬───────┘                  │
│                                                   │                          │
│                                                   ▼                          │
│                                           ┌──────────────┐                  │
│                                           │   Content    │                  │
│                                           │  Generation  │                  │
│                                           │   (Planned)  │                  │
│                                           └──────────────┘                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Google Ads API Integration

### 3.1 Current Integration: Keyword Planner

**Purpose:** Enrich extracted article keywords with search intelligence to prioritize content creation.

**API Services Used:**
- `KeywordPlanIdeaService.GenerateKeywordHistoricalMetrics`
- `KeywordPlanIdeaService.GenerateKeywordIdeas`

**Data Retrieved:**
| Field | Description | Use Case |
|-------|-------------|----------|
| avg_monthly_searches | Search volume | Prioritize high-demand topics |
| competition | LOW/MEDIUM/HIGH | Identify content opportunities |
| competition_index | 0-100 scale | Refine content strategy |
| top_of_page_bid | CPC estimates | Budget planning for paid promotion |

**Request Patterns:**
- Frequency: Every 2 hours (batch processing)
- Volume: ~50-100 keywords per batch
- Records: ~10 articles per batch
- Daily Estimate: 600-1,200 keyword lookups

**Authentication:**
- OAuth 2.0 with refresh token
- Manager account for API access
- Secure credential storage in AWS Secrets Manager

### 3.2 Data Handling

**Storage:**
- Keyword metrics stored in Airtable (our content database)
- No Google Ads customer data is stored externally
- Metrics used solely for internal content prioritization

**Retention:**
- Keyword metrics refreshed with each article processing
- Historical metrics not retained beyond current values
- No PII or customer data involved

### 3.3 Technical Implementation

```python
# Simplified code structure (actual implementation)

def get_keyword_metrics(client, customer_id, keywords):
    """
    Retrieve historical metrics for keywords.
    Used to prioritize content creation based on search demand.
    """
    request = client.get_type("GenerateKeywordHistoricalMetricsRequest")
    request.customer_id = customer_id
    request.keywords.extend(keywords)
    request.language = "languageConstants/1000"  # English
    request.geo_target_constants.append("geoTargetConstants/2840")  # US

    response = keyword_plan_idea_service.generate_keyword_historical_metrics(
        request=request
    )

    return [
        {
            'keyword': result.text,
            'volume': result.keyword_metrics.avg_monthly_searches,
            'competition': result.keyword_metrics.competition.name,
            'cpc_low': result.keyword_metrics.low_top_of_page_bid_micros / 1e6,
            'cpc_high': result.keyword_metrics.high_top_of_page_bid_micros / 1e6
        }
        for result in response.results
    ]
```

---

## 4. Future Roadmap: Automated Campaign Management

### 4.1 Phase 2: Campaign Performance Integration (Q2 2026)

**Objective:** Connect content performance to campaign optimization.

**Planned API Services:**
- `GoogleAdsService.Search` - Retrieve campaign performance data
- `GoogleAdsService.SearchStream` - Real-time performance monitoring

**Use Cases:**
| Capability | Description |
|------------|-------------|
| Performance Tracking | Monitor which content-driven keywords convert |
| ROI Analysis | Connect content investment to campaign revenue |
| Audience Insights | Understand which topics resonate with converters |

### 4.2 Phase 3: Automated Campaign Optimization (Q3-Q4 2026)

**Objective:** Use content intelligence to optimize campaign targeting.

**Planned API Services:**
- `KeywordPlanService` - Create and manage keyword plans
- `CampaignService` - Adjust campaign targeting based on content performance
- `AdGroupService` - Optimize ad group structure
- `AdGroupCriterionService` - Manage keyword targeting

**Automation Workflows:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CAMPAIGN PERFORMANCE MANAGEMENT SYSTEM                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌────────────────────────────────────────────────────────────────────┐    │
│   │                     CONTENT INTELLIGENCE LOOP                       │    │
│   │                                                                     │    │
│   │   Content Engine ──▶ Keywords ──▶ Campaign Targeting               │    │
│   │         ▲                              │                            │    │
│   │         │                              ▼                            │    │
│   │   Insights ◀────────────────── Performance Data                    │    │
│   │                                                                     │    │
│   └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐        │
│   │   DISCOVERY     │    │   OPTIMIZATION  │    │   SCALING       │        │
│   ├─────────────────┤    ├─────────────────┤    ├─────────────────┤        │
│   │ • New keyword   │    │ • Bid adjust-   │    │ • Budget allo-  │        │
│   │   opportunities │    │   ments based   │    │   cation across │        │
│   │ • Trending      │    │   on content    │    │   top-performing│        │
│   │   topics        │    │   performance   │    │   keywords      │        │
│   │ • Competitive   │    │ • Negative      │    │ • Campaign      │        │
│   │   gaps          │    │   keyword       │    │   expansion     │        │
│   │                 │    │   additions     │    │                 │        │
│   └─────────────────┘    └─────────────────┘    └─────────────────┘        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Planned Automation Rules:**

1. **Keyword Discovery Pipeline**
   - Content engine identifies high-value keywords from news analysis
   - System checks if keywords exist in campaigns
   - Recommends new keywords for campaign managers to approve

2. **Performance-Based Optimization**
   - Track which content topics drive conversions
   - Increase bids on keywords aligned with converting content
   - Reduce spend on underperforming keyword themes

3. **Budget Allocation Intelligence**
   - Analyze seasonal trends in content topics
   - Recommend budget shifts toward trending opportunities
   - Alert on emerging topics with high conversion potential

### 4.3 Governance and Controls

**Human Oversight:**
- All campaign changes require human approval
- Automated recommendations with manual execution
- Daily summary reports for marketing team review

**Budget Safeguards:**
- Maximum bid caps enforced
- Daily budget limits per campaign
- Anomaly detection for unusual spend patterns

**Compliance:**
- Google Ads policies enforced at content generation
- No prohibited content categories
- Trademark and brand safety checks

---

## 5. Architecture Design

### 5.1 System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS CLOUD                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                        SERVERLESS COMPUTE                            │   │
│   │                                                                      │   │
│   │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│   │   │  Collector   │  │  Processor   │  │ SEO Enricher │              │   │
│   │   │   Lambda     │  │   Lambda     │  │    Lambda    │              │   │
│   │   │              │  │              │  │              │              │   │
│   │   │ • RSS fetch  │  │ • AI extract │  │ • Keyword    │              │   │
│   │   │ • Relevance  │  │ • Emotional  │  │   Planner    │              │   │
│   │   │   scoring    │  │   angles     │  │ • Metrics    │              │   │
│   │   │ • Queuing    │  │ • Authority  │  │ • Related    │              │   │
│   │   │              │  │   signals    │  │   keywords   │              │   │
│   │   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │   │
│   │          │                 │                 │                       │   │
│   └──────────┼─────────────────┼─────────────────┼───────────────────────┘   │
│              │                 │                 │                           │
│   ┌──────────┼─────────────────┼─────────────────┼───────────────────────┐   │
│   │          ▼                 ▼                 ▼                       │   │
│   │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │   │
│   │   │  EventBridge │  │     SQS      │  │   Secrets    │              │   │
│   │   │  (Schedules) │  │   (Queue)    │  │   Manager    │              │   │
│   │   └──────────────┘  └──────────────┘  └──────────────┘              │   │
│   │                           SUPPORTING SERVICES                        │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   │ HTTPS
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL SERVICES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐     │
│   │                  │    │                  │    │                  │     │
│   │    Airtable      │    │   Google Ads     │    │   Amazon         │     │
│   │    (Database)    │    │   API            │    │   Bedrock        │     │
│   │                  │    │                  │    │   (AI/ML)        │     │
│   │  • Articles      │    │  • Keyword       │    │                  │     │
│   │  • Keywords      │    │    Planner       │    │  • Relevance     │     │
│   │  • SEO metrics   │    │  • Campaign      │    │    scoring       │     │
│   │  • Content       │    │    Management    │    │  • Extraction    │     │
│   │                  │    │    (Future)      │    │  • Generation    │     │
│   │                  │    │                  │    │                  │     │
│   └──────────────────┘    └──────────────────┘    └──────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Security Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SECURITY CONTROLS                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   AUTHENTICATION                    DATA PROTECTION                          │
│   ┌────────────────────┐           ┌────────────────────┐                   │
│   │ • OAuth 2.0        │           │ • TLS 1.3 in       │                   │
│   │ • Refresh tokens   │           │   transit          │                   │
│   │ • IAM roles        │           │ • AES-256 at rest  │                   │
│   │ • Least privilege  │           │ • No PII stored    │                   │
│   └────────────────────┘           └────────────────────┘                   │
│                                                                              │
│   SECRETS MANAGEMENT               ACCESS CONTROL                            │
│   ┌────────────────────┐           ┌────────────────────┐                   │
│   │ • AWS Secrets      │           │ • VPC isolation    │                   │
│   │   Manager          │           │ • Security groups  │                   │
│   │ • Automatic        │           │ • API rate limits  │                   │
│   │   rotation         │           │ • Audit logging    │                   │
│   └────────────────────┘           └────────────────────┘                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.3 Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Compute | AWS Lambda (Python 3.12) | Serverless function execution |
| Queue | Amazon SQS | Async message processing |
| Scheduler | Amazon EventBridge | Periodic job triggers |
| Secrets | AWS Secrets Manager | Credential storage |
| AI/ML | Amazon Bedrock | Content analysis and extraction |
| Database | Airtable | Content and metadata storage |
| Ads API | Google Ads API v22 | Keyword and campaign intelligence |
| SDK | google-ads Python | API client library |

---

## 6. API Usage Projections

### 6.1 Current Phase (Keyword Planner Only)

| Metric | Value |
|--------|-------|
| API Calls per Day | 600-1,200 |
| Keywords per Request | 10-20 |
| Requests per Batch | 10-15 |
| Batches per Day | 12 (every 2 hours) |

### 6.2 Phase 2 Projections (Performance Integration)

| Metric | Value |
|--------|-------|
| Keyword Planner Calls | 1,200/day |
| Campaign Query Calls | 100/day |
| Total Daily Operations | ~1,500 |

### 6.3 Phase 3 Projections (Campaign Management)

| Metric | Value |
|--------|-------|
| Keyword Planner Calls | 2,000/day |
| Campaign Operations | 500/day |
| Optimization Queries | 300/day |
| Total Daily Operations | ~3,000 |

---

## 7. Compliance and Terms

### 7.1 Google Ads API Terms Compliance

- We will comply with all Google Ads API Terms of Service
- We will not resell or redistribute API data
- We will respect rate limits and quotas
- We will maintain required disclosures to end users

### 7.2 Data Use Declaration

- Keyword Planner data is used solely for internal content optimization
- No Google Ads customer data is shared with third parties
- Campaign data is used only for our own account optimization
- We do not build competing advertising products

### 7.3 Contact Information

**Technical Contact:**
[Your Name]
[Your Email]
[Your Phone]

**Business Contact:**
AI Projects for Kids, LLC
[Business Address]

---

## 8. Appendix

### A. Sample API Request/Response

**Request: GenerateKeywordHistoricalMetrics**
```json
{
  "customer_id": "9236040286",
  "keywords": ["AI education for kids", "coding for children"],
  "language": "languageConstants/1000",
  "geo_target_constants": ["geoTargetConstants/2840"]
}
```

**Response (Processed):**
```json
[
  {
    "keyword": "AI education for kids",
    "volume": 2400,
    "competition": "MEDIUM",
    "competition_index": 45,
    "cpc_low": 1.25,
    "cpc_high": 3.50
  },
  {
    "keyword": "coding for children",
    "volume": 12100,
    "competition": "HIGH",
    "competition_index": 72,
    "cpc_low": 2.10,
    "cpc_high": 5.80
  }
]
```

### B. Lambda Function Structure

```
lambdas/
├── collector/
│   └── lambda_function.py    # RSS collection and filtering
├── processor/
│   └── lambda_function.py    # AI extraction and analysis
└── seo-enricher/
    ├── lambda_function.py    # Google Ads API integration
    └── requirements.txt      # Dependencies
```

### C. Airtable Schema (Relevant Fields)

| Field | Type | Source |
|-------|------|--------|
| article_keywords | text | Processor Lambda |
| seo_target_keywords | JSON | SEO Enricher (Keyword Planner) |
| seo_long_tail_keywords | JSON | SEO Enricher (Keyword Ideas) |

---

*Document prepared for Google Ads API Basic Access application.*
