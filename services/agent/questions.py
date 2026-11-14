"""Intake questions: a base set every business answers, plus a sector set chosen from the business type.

Each question names the signal it unlocks (`why`), so the owner sees why it is asked and the factor-analyst knows
what to look for. Questions are chosen by the same router as the stock packs (tools/packs.sector_for), so typing
"food pantry" loads the food bank set, "laundromat" the retail/services set, "orthopedic clinic" the healthcare set.

Answers travel in existing contract fields: customers, services, languages, payers_accepted, existing_offices,
constraints, and everything else as labeled lines in free_text ("Question: answer"), which the factor-analyst reads.
"""

from typing import Literal

from pydantic import BaseModel, Field

from contracts.models import PracticeProfile
from services.agent.tools.packs import pack_for, sector_for

Kind = Literal["text", "long", "choice", "multi", "locations"]


class Question(BaseModel):
    id: str
    text: str
    why: str  # the signal it unlocks, shown under the question
    kind: Kind = "text"
    options: list[str] = Field(default_factory=list)
    placeholder: str = ""
    required: bool = False
    field: str = "free_text"  # where the answer goes: customers | services | languages | payers | free_text


class QuestionSet(BaseModel):
    sector: str  # pack id, e.g. "nonprofit.food_bank"
    label: str  # "Food bank / pantry"
    base: list[Question]
    sector_questions: list[Question]


Q = Question

BASE = [
    Q(id="locations", kind="locations", required=True, field="existing_offices",
      text="Where are your current locations?",
      why="Your own sites are the strongest signal we have: we learn what works where you already succeed, and "
          "we subtract the customers they already serve so a new site isn't just stealing from them.",
      placeholder="Name and street address of each site, or tick 'This is our first location'"),
    Q(id="best_location", kind="long",
      text="Which current location does best, which struggles, and why do you think so?",
      why="Turns your sites into labeled examples: the neighborhood traits of your best site become factors to "
          "look for."),
    Q(id="customers", kind="long", required=True, field="customers",
      text="Who are your customers? Ages, household type, income, languages, how they pay.",
      why="Picks the population we count as demand in every neighborhood.",
      placeholder="e.g. working parents with kids under 5, mostly Spanish-speaking, many pay with subsidies"),
    Q(id="customer_origin", kind="text",
      text="Where do your customers come from today? ZIP codes, neighborhoods, or how far they travel.",
      why="Sets how far people will come (the catchment) and shows which areas already know you."),
    Q(id="access", kind="multi", options=["Drive", "Bus or rail", "Walk", "Bike", "Get dropped off", "We go to them"],
      text="How do customers usually get to you?",
      why="Weighs parking against transit and walkability."),
    Q(id="unique", kind="long",
      text="What makes you different from others who do the same thing?",
      why="Tells us which competitors really compete with you and what kind of area rewards your edge."),
    Q(id="pull", kind="long",
      text="What about a neighborhood would pull people to your door?",
      why="Nuanced local factors (schools, churches, offices, grocery anchors, safety) that the scouts go find data for."),
    Q(id="competitors", kind="text",
      text="Who do you compete with, or who else serves the same people?",
      why="We map these places so net-new customers account for them."),
    Q(id="space", kind="text",
      text="What space do you need? Size, rent budget, parking, ground floor, hours.",
      why="Hard filters on real listings; anything outside them is excluded, not ranked low."),
]

HEALTHCARE = [
    Q(id="payers", kind="multi", required=True, field="payers",
      options=["Medicare", "Medicaid", "CHIP", "Commercial", "Workers' comp", "Self-pay"],
      text="Which insurance do you accept, and roughly what share of patients is each?",
      why="Demand counts only residents whose coverage you accept."),
    Q(id="referrals", kind="text",
      text="Where do referrals come from? (primary care, hospitals, schools, word of mouth)",
      why="Referral sources become a 'referral base' factor near each site."),
    Q(id="services_hc", kind="text", field="services",
      text="Which services or procedures matter most? Any equipment rooms (x-ray, MRI, chairs, dialysis stations)?",
      why="Shapes the specialty pack and the space a site must fit."),
    Q(id="clinicians", kind="text",
      text="How many clinicians per location, and how many patients does a location see in a week?",
      why="Lets us size a new site's capacity against the demand it can win."),
    Q(id="patient_age", kind="text",
      text="What age groups are most of your patients?",
      why="Weights demand by the ages that actually use the specialty."),
]

FOOD_BANK = [
    Q(id="program_type", kind="multi", required=True, field="services",
      options=["Choice pantry", "Pre-packed boxes", "Mobile / drive-through distribution", "Hot meals",
               "Senior boxes (CSFP)", "Kids' weekend backpacks", "SNAP enrollment help", "Delivery to homebound"],
      text="What does this site do?",
      why="A mobile distribution needs a parking lot near the need; a warehouse needs a dock; a choice pantry needs "
          "walk-in clients nearby."),
    Q(id="who_served", kind="long", required=True,
      text="Who do you serve most? (families with kids, seniors, working poor, unhoused, students, veterans, immigrants)",
      why="Chooses which need counts as demand: poverty, children or seniors in poverty, SNAP households."),
    Q(id="eligibility", kind="choice", options=["No requirements", "Below 185% of poverty (TEFAP)", "Below 200%",
                                                  "Referral or ID needed", "Service area by ZIP"],
      text="Who is eligible?", why="Matches the income line we count in each neighborhood."),
    Q(id="volume", kind="text",
      text="How many households do you serve per week now, and how many could the new site serve?",
      why="Sizes the catchment and lets us flag sites that would be overwhelmed or underused."),
    Q(id="client_travel", kind="choice", options=["Mostly walk or bus", "Mostly drive", "Mix", "We deliver"],
      text="How do clients reach you?",
      why="Clients without a car weigh bus stops and short distances heavily; USDA low-vehicle tracts come in here."),
    Q(id="network", kind="text",
      text="Are you a partner agency of a regional food bank (e.g. Houston Food Bank)? Where are partner pantries?",
      why="Nearby partners are gaps or duplicates; we look for areas no one covers."),
    Q(id="logistics", kind="text",
      text="Do you need a loading dock, cold storage, freezer space, or room for a truck?",
      why="Hard filters on the space: industrial and flex listings come in when you need a dock."),
    Q(id="hours", kind="text",
      text="What days and hours are you open? (evenings and weekends reach working families)",
      why="Daytime job density and transit service hours matter more for evening distributions."),
    Q(id="hosts", kind="text",
      text="Could a church, school or community center host you instead of a lease?",
      why="Partner sites become a factor and widen the search beyond listings."),
]

NONPROFIT = [
    Q(id="mission", kind="long", required=True, field="services",
      text="What services will this site offer? (case management, shelter, job training, health, legal, youth programs)",
      why="Chooses which kind of need counts as demand."),
    Q(id="who_served", kind="long", required=True,
      text="Who do you serve? (age, income, housing status, language, immigration status)",
      why="Picks the population we count: poverty, children or seniors in poverty, limited English."),
    Q(id="funding", kind="text",
      text="Are you funded to serve a specific area or population (grant service area, county, ZIP codes)?",
      why="Funder boundaries become hard rules on where a site can go."),
    Q(id="volume", kind="text",
      text="How many people do you serve per week now, and how many at the new site?",
      why="Sizes the catchment and capacity."),
    Q(id="client_travel", kind="choice", options=["Mostly walk or bus", "Mostly drive", "Mix", "We go to them"],
      text="How do clients reach you?", why="Weighs transit, distance and households without a car."),
    Q(id="partners", kind="text",
      text="Which partners refer clients to you or share space? (schools, clinics, churches, shelters)",
      why="Partner locations become a factor; their data is something the scouts can find."),
    Q(id="safety", kind="text",
      text="Any needs around privacy, safety or visibility? (confidential location, discreet entrance)",
      why="Some services want discretion, not foot traffic."),
]

RETAIL = [
    Q(id="ticket", kind="text",
      text="What does a typical customer spend per visit, and how often do they come back?",
      why="Frequent, low-ticket visits mean a short catchment; we size the trip from this."),
    Q(id="peak", kind="text",
      text="When are you busiest? (weekday evenings, weekends, mornings)",
      why="Commuter flows vs. residents at home change which demand we count."),
    Q(id="renters", kind="choice", options=["Yes, mostly renters/apartments", "Mixed", "Mostly homeowners", "Not sure"],
      text="Do your customers mostly live in apartments? (laundromats: no in-unit washers)",
      why="Turns renters and multifamily housing into the demand signal."),
    Q(id="anchors", kind="text",
      text="What neighbors help you? (grocery anchor, discount store, schools, laundromat next to a taqueria...)",
      why="Co-tenancy becomes a factor the scouts look for."),
    Q(id="visibility", kind="choice", options=["Need a busy street", "Strip center is fine", "Destination, visibility doesn't matter"],
      text="How much does visibility from the road matter?", why="Weighs traffic and frontage on real listings."),
    Q(id="services_retail", kind="text", field="services",
      text="What do you sell or offer? (wash-and-fold, self-serve, repairs, cuts and color...)",
      why="Sharpens who competes with you."),
]

RESTAURANT = [
    Q(id="format", kind="choice", options=["Quick service / counter", "Full service", "Cafe / coffee", "Bakery",
                                           "Food truck / ghost kitchen", "Bar"],
      text="What format?", why="Quick service lives on lunch workers; full service on dinner residents."),
    Q(id="price", kind="text", text="What does a typical check cost per person?",
      why="Matches household income to your price point."),
    Q(id="dayparts", kind="multi", options=["Breakfast", "Lunch", "Dinner", "Late night", "Weekend brunch"],
      text="Which meals drive sales?", why="Daytime jobs vs. residents vs. nightlife."),
    Q(id="cuisine", kind="text", field="services", text="What cuisine, and who craves it most?",
      why="Same-cuisine competition and the communities most likely to come."),
    Q(id="drive_thru", kind="choice", options=["Need a drive-thru", "Nice to have", "No"],
      text="Drive-thru, patio or delivery-heavy?", why="Filters sites by pad, parking and frontage."),
]

CHILDCARE = [
    Q(id="ages", kind="multi", required=True, field="services",
      options=["Infants", "Toddlers", "Pre-K (3-4)", "School age / after-school", "Tutoring K-12"],
      text="Which ages do you serve?", why="Chooses whether we count children under 5 or school-age kids."),
    Q(id="tuition", kind="text",
      text="What is tuition, and do you accept Texas Workforce Commission child care subsidies?",
      why="Subsidy-accepting centers count children in poverty as demand; private-pay counts higher incomes."),
    Q(id="capacity", kind="text", text="How many children per location, and how full are you now?",
      why="Licensed capacity nearby is the competition we compare against."),
    Q(id="work_or_home", kind="choice", options=["Near parents' homes", "Near parents' jobs", "Near schools", "Mixed"],
      text="Do parents choose you near home, near work, or near school?",
      why="Decides whether jobs or residents drive demand."),
    Q(id="outdoor", kind="text", text="Do you need outdoor play space, a drop-off lane, or a specific license type?",
      why="Hard rules on space and site."),
]

FITNESS = [
    Q(id="format_fit", kind="choice", options=["Big-box gym", "Boutique studio", "Martial arts / boxing",
                                               "Climbing", "Personal training", "Youth sports"],
      text="What format?", why="Big-box needs a large catchment; boutique studios need dense, higher-income areas."),
    Q(id="membership", kind="text", text="What does a membership cost, and how often do members come?",
      why="Matches income and sets the trip distance."),
    Q(id="members", kind="text", text="Who are your members? (age, gender, students, office workers, families)",
      why="Weights adults 18-44 vs. 45-64 and daytime workers."),
    Q(id="class_times", kind="text", text="When are classes busiest? (before work, lunch, evenings)",
      why="Commute routes and jobs density for morning and lunch classes."),
]

GENERAL = [
    Q(id="offer", kind="text", field="services", text="What do you sell or offer?",
      why="Sharpens who competes with you."),
    Q(id="frequency", kind="text", text="How often does a customer come, and how far would they drive?",
      why="Sets how far people will come (the catchment)."),
]

SECTORS: dict[str, tuple[str, list[Question]]] = {
    "healthcare": ("Medical practice", HEALTHCARE),
    "nonprofit.food_bank": ("Food bank or pantry", FOOD_BANK),
    "nonprofit.base": ("Nonprofit or social services", NONPROFIT),
    "retail.services": ("Retail or service shop", RETAIL),
    "food.restaurant": ("Restaurant or cafe", RESTAURANT),
    "education.childcare": ("Child care or education", CHILDCARE),
    "fitness.base": ("Gym or fitness studio", FITNESS),
    "general.base": ("Any business", GENERAL),
}
HEALTH_WORDS = ("clinic", "medical", "doctor", "physician", "dental", "dentist", "orthodont", "pediatric", "dialysis",
                "therapy", "therapist", "chiropract", "urgent care", "optometr", "dermatolog", "cardiolog", "practice",
                "health center", "hospital", "orthopedic", "orthopaedic", "nephrolog", "podiatr", "mental health",
                "counseling", "psychiatr", "physical therapy", "rehab", "pharmacy", "veterinar", "imaging")


def sector_of(text: str, specialty: str = "") -> str:
    """Business type text (and healthcare specialty) -> question set id. Social services win over 'health' words."""
    if specialty:
        return "healthcare"
    sector = sector_for(text)
    if sector and sector.startswith("nonprofit."):
        return sector
    low = text.lower()
    if any(w in low for w in HEALTH_WORDS):
        return "healthcare"
    return sector or "general.base"


def questions_for(text: str, specialty: str = "") -> QuestionSet:
    sector = sector_of(text, specialty)
    label, qs = SECTORS[sector]
    return QuestionSet(sector=sector, label=label, base=BASE, sector_questions=qs)


def profile_sector(profile: PracticeProfile) -> str:
    return sector_of(profile.business_type or profile.name, profile.specialty)


def required_ids(sector: str) -> list[str]:
    return [q.id for q in (*BASE, *SECTORS[sector][1]) if q.required]


def pack_id(profile: PracticeProfile) -> str:
    return pack_for(profile).id
