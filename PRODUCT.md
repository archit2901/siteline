# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Business owners and their operations or expansion leads** deciding where in Houston to open the next location: a laundromat, a coffee shop, a tutoring center, a medical practice. They use Siteline at a laptop as a working tool: describe the business, approve the agent's data plan and weights, then compare real spaces.
- **Hackathon judges** (HackPrinceton Fall 2026, Business + Enterprise main track; also CoStar, LangChain, Peri and Amazon) see it projected in a two-minute live demo. The same screens must read from across a room.

Confirmed by the user (Nov 14): the frontend serves both the demo and daily use, so the demo shows how the product is really used.

## Product Purpose

Siteline tells any business where in Houston to open its next location. The owner describes the business and its customers. An agent then:
1. Works out who the customers are, who competes and what space fits.
2. Finds public data to measure what matters.
3. Asks for approval twice: the data plan, then the weights.

Python then scores every H3 hexagon, estimates the customers each real CoStar space would win after competitors and the owner's other locations take their share, and verifies every number in the report.

Success means the owner leaves with a short ranked list of real spaces they could lease, and knows why each one is there.

## Positioning

Generic location tools treat "retail" or "healthcare" as one category. Siteline reasons per business: a laundromat and an orthopedic group get different customers, competitors, data, factors and maps. The model decides what to compute; Python computes every number; a critic checks each number in each claim before it is shown.

## Operating Context

The run is a sequence the user watches and steers:
1. Intake.
2. Plan steps.
3. Parallel data scouts that search, preview and ingest datasets live.
4. Approval of the data plan.
5. Approval of the weights.
6. Zone scoring on the map.
7. Real-space search, then ranking.
8. A verified report.

Two run sources share the same screens:
- **Live agent:** Amazon Nova on Bedrock.
- **Recorded run:** a saved live run replayed by the server or the browser, which pauses at approvals the same way.

A run takes about 10–60 seconds. Results include, per site:
- customers won (net-new, or reach for businesses other than healthcare);
- annual rent per customer won;
- size, rent, floor and parking;
- a Matterport tour when one exists;
- stability across alternative weightings;
- verified claims with sources.

## Capabilities and Constraints

- **Region:** Houston only (Harris, Fort Bend and Montgomery counties). There are 11,720 H3 res-8 cells. CoStar spaces cover inner Harris only.
- **Contracts:** the web app consumes `contracts/` types (RunEvent, Factor, Report, Recommendation, ScoreResponse) and must not invent fields.
- **Stack:** React + Vite + TypeScript strict, MapLibre with deck.gl H3 layers, and SSE from agent-api.
- **Data:** never shows CoStar data in committed fixtures. Fixture sites are invented.
- **Wording:** "patients" for healthcare; for other businesses, the business's demand label (for example "renter households"), presented as reach rather than a sales forecast.
- **Undecided:** weight sliders (later), Jacksonville zones.

## Brand Commitments

The name is Siteline. A hexagon mark exists in the current UI; the user has not made it binding.

## Evidence on Hand

- `fixtures/` holds invented demo data.
- `data/golden/` holds recorded real runs for the laundromat and the orthopedic group. They are never committed.
- `web/src/presets/presets.json` holds seven example businesses.
- **Not available:** no testimonials or customer quotes are on hand. Dr. Deshmukh's quote is pending from Jayan and must not be fabricated.

## Product Principles

1. Show the agent working: the reasoning, the data it finds and the approvals are the product, not a loading screen.
2. Every number traces to a source: claims show where they come from, and nothing on screen is model-written.
3. The answer is a decision: real spaces, ranked, with what each would win and cost.
4. Legible at two meters and comfortable at arm's length: the same screens serve the projector and the laptop.

## Accessibility & Inclusion

Projector legibility: large type, strong contrast, and color never the only carrier of meaning (status also in text). Respect reduced motion. The current UI uses Atkinson Hyperlegible for legibility; this is evidence, not a binding commitment.
