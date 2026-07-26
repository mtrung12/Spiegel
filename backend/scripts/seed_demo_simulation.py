"""
Seed a finished demo simulation, so the report views have something to render
without paying for a real OASIS run.

The demo is a brand and employer-brand campaign for the Asseco Group. Asseco is
a real company and the campaign copy is drawn from its own public positioning,
but EVERY audience reaction below is fabricated for testing. The personas do
not correspond to real people and the reactions are not market research about
Asseco - they are fixture data shaped to look like plausible B2B chatter, so
the sentiment digest, the KPI panel and the feed board can be exercised.

Usage:
    cd backend && uv run python scripts/seed_demo_simulation.py
    cd backend && uv run python scripts/seed_demo_simulation.py --delete
"""

import argparse
import json
import os
import random
import sqlite3
import shutil
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.models.project import ProjectManager, ProjectStatus  # noqa: E402

SIM_ID = "sim-asseco-demo"
PROJECT_ID = "proj-asseco-demo"
GRAPH_ID = "demo_graph_asseco"

SIM_DIR = os.path.join(
    os.path.dirname(__file__), '..', 'uploads', 'simulations', SIM_ID
)

CAMPAIGN_BRIEF = (
    "Asseco Group brand and employer-brand campaign. Asseco is a federation of "
    "software companies led by Asseco Poland, building mission-critical systems "
    "for banks, energy and telecom operators, the public sector and healthcare "
    "across Europe and Israel. The campaign message is 'technology that runs "
    "the everyday' - dependable software behind services people use without "
    "thinking about them. Target audience: enterprise IT decision makers, "
    "public-sector buyers, healthcare IT leads, and software engineers "
    "considering where to work."
)

# ── The campaign creative, seeded into the feed before round 0 ──────────
SEED_POSTS = [
    "Every day, millions of payments clear, prescriptions get filled and grid "
    "loads get balanced. You never think about the software underneath. That is "
    "the point. Asseco - technology that runs the everyday.",

    "Started as one of Poland's first software startups. Today: a federation of "
    "companies, systems in banks, hospitals, utilities and public administration "
    "across Europe and Israel. We take responsibility for what we ship, because "
    "our systems cannot fail.",

    "We are hiring engineers who want their code to matter at 3am. Core banking, "
    "healthcare records, energy settlement. Not another dashboard. #Asseco",
]

# ── Audience segments, mirroring what the ontology would produce ────────
SEGMENTS = {
    "BankingClient": "Banking and financial services decision makers",
    "PublicSectorBuyer": "Public administration procurement and IT leads",
    "HealthcareITLead": "Hospital and clinic IT management",
    "TelcoArchitect": "Telecom and energy solution architects",
    "SoftwareEngineer": "Developers evaluating the company as an employer",
    "IndustryAnalyst": "Analysts and technology press",
    "Investor": "Shareholders and market watchers",
    "Competitor": "Rival vendors and integrators",
}

# (agent_id, display name, handle, segment, stance)
AGENTS = [
    (0, "Marek Wolski", "mwolski_core", "BankingClient", "neutral"),
    (1, "Ilona Baranek", "ibaranek", "BankingClient", "supportive"),
    (2, "Tomasz Reder", "t_reder", "BankingClient", "opposing"),
    (3, "Katarzyna Lis", "klis_fin", "BankingClient", "neutral"),
    (4, "Grzegorz Pawlak", "gpawlak_gov", "PublicSectorBuyer", "opposing"),
    (5, "Anna Dabrowa", "anna_dabrowa", "PublicSectorBuyer", "neutral"),
    (6, "Piotr Zielinski", "pziel_it", "PublicSectorBuyer", "supportive"),
    (7, "Dr Ewa Marciniak", "ewa_marciniak", "HealthcareITLead", "supportive"),
    (8, "Radek Sobczyk", "rsobczyk_hit", "HealthcareITLead", "opposing"),
    (9, "Monika Urban", "monika_urban", "HealthcareITLead", "neutral"),
    (10, "Damian Kot", "dkot_arch", "TelcoArchitect", "neutral"),
    (11, "Lukas Varga", "lvarga_net", "TelcoArchitect", "opposing"),
    (12, "Beata Nowicka", "bnowicka", "TelcoArchitect", "supportive"),
    (13, "Kamil Ostrowski", "kamil_dev", "SoftwareEngineer", "opposing"),
    (14, "Zofia Krol", "zkrol_dev", "SoftwareEngineer", "supportive"),
    (15, "Bartek Nowak", "bnowak_codes", "SoftwareEngineer", "opposing"),
    (16, "Julia Wieczorek", "jwieczorek", "SoftwareEngineer", "neutral"),
    (17, "Adam Stec", "adamstec", "SoftwareEngineer", "supportive"),
    (18, "Hana Brachtl", "hbrachtl", "IndustryAnalyst", "neutral"),
    (19, "Viktor Ilic", "vilic_research", "IndustryAnalyst", "supportive"),
    (20, "Elena Popescu", "epopescu", "IndustryAnalyst", "opposing"),
    (21, "Rafal Gorski", "rgorski_cap", "Investor", "supportive"),
    (22, "Sandra Klein", "sklein_equity", "Investor", "neutral"),
    (23, "Oskar Duda", "oduda", "Competitor", "opposing"),
    (24, "Nadia Farkas", "nfarkas", "Competitor", "opposing"),
]

# The silent majority. In a real run most of the audience is exposed to the
# campaign and never reacts to it, which is what makes passive share and
# engagement rate meaningful. These personas only ever appear as DO_NOTHING.
LURKERS = [
    (25, "Jakub Wesolowski", "jwesolowski", "BankingClient", "neutral"),
    (26, "Petra Novakova", "pnovakova", "BankingClient", "observer"),
    (27, "Milan Horvat", "mhorvat", "PublicSectorBuyer", "observer"),
    (28, "Agnieszka Rybak", "arybak", "HealthcareITLead", "neutral"),
    (29, "Stefan Kovac", "skovac", "TelcoArchitect", "observer"),
    (30, "Weronika Adamska", "wadamska", "SoftwareEngineer", "neutral"),
    (31, "Tobias Lang", "tlang", "SoftwareEngineer", "observer"),
    (32, "Irena Blazek", "iblazek", "IndustryAnalyst", "observer"),
    (33, "Pawel Mroz", "pmroz", "Investor", "neutral"),
    (34, "Dorota Sikora", "dsikora", "PublicSectorBuyer", "neutral"),
]

ALL_AGENTS = AGENTS + LURKERS

# ── Fabricated audience reactions ───────────────────────────────────────
# (agent_id, platform, round, text, likes, dislikes)
POSTS = [
    (2, "twitter", 2, "Nice ad. Now ask anyone who has tried to migrate off their core banking stack how 'the everyday' feels. Five year exit plan minimum.", 87, 4),
    (1, "twitter", 2, "Say what you want, our settlement layer has not had an unplanned outage in four years. That is not nothing in this industry.", 64, 2),
    (13, "reddit", 3, "Worked there 2 years. The systems genuinely matter. The tooling around them is from 2011 and nobody is allowed to touch it because a bank depends on it.", 142, 6),
    (4, "twitter", 3, "Public tender reality check: we waited eleven months for a change request that was quoted at six weeks. 'We take full responsibility' is doing heavy lifting.", 96, 3),
    (7, "twitter", 4, "Our hospital ran on their records system through two ransomware waves in the region and never lost a chart. I will take boring and unbreakable.", 118, 1),
    (23, "twitter", 4, "Impressive federation. Also impressive: the number of overlapping products inside it. Which one do I actually buy?", 71, 8),
    (14, "reddit", 4, "Counterpoint to the usual complaints here - I have had genuine work life balance, no weekend deploys, and my mortgage got approved without anyone blinking. Stability is a feature.", 88, 5),
    (11, "twitter", 5, "Cloud story is where this falls apart for me. Everything is 'we can host it' rather than 'it is cloud native'. In 2026 that is a real gap.", 103, 2),
    (19, "twitter", 5, "People underestimate how hard regulated software is. Anyone can ship a fintech app. Very few can keep a national settlement system compliant across six jurisdictions.", 79, 3),
    (15, "reddit", 5, "Salary bands are the elephant in the room. I got a 40% raise moving to a product company doing objectively easier work. The mission talk does not pay rent.", 167, 9),
    (8, "twitter", 6, "Support responsiveness has genuinely degraded since the last reorg. Ticket sat for nine days. Nine. On a clinical system.", 91, 2),
    (6, "twitter", 6, "For once a vendor whose consultants actually speak Polish, understand our procurement law and show up on site. That is worth more than a slick UI.", 68, 4),
    (20, "twitter", 6, "The acquisition strategy has built scale but not coherence. Reading their segment reporting is a part time job.", 74, 5),
    (17, "reddit", 7, "Genuinely proud of what we built for the healthcare side. My mother's clinic uses it. Not many jobs where you can say that.", 95, 3),
    (10, "twitter", 7, "Evaluated three of their telco products last quarter. Deep domain knowledge, no argument. Integration effort was double what was scoped.", 82, 1),
    (24, "twitter", 7, "'Technology that runs the everyday' is a lovely way of saying 'legacy systems you cannot replace'.", 129, 11),
    (21, "twitter", 8, "Steady dividend, mission critical contracts, low churn customers. Boring in the best possible way for a portfolio.", 56, 2),
    (5, "twitter", 8, "Attended their public sector day. Solid roadmap presentation, genuinely useful compliance session.", 34, 0),
    (16, "reddit", 8, "Interviewed there recently. Process was slow but respectful, and the technical questions were about real problems rather than leetcode. Undecided honestly.", 61, 1),
    (3, "twitter", 9, "The onboarding documentation is thorough to the point of being unusable. 400 pages before you can configure a product.", 77, 3),
    (12, "twitter", 9, "Migrated our billing platform with them last year. On time, on budget, and they caught two errors in OUR spec. Credit where due.", 84, 2),
    (9, "twitter", 9, "Neutral observation: their healthcare install base is larger than most people realise. Half the clinics I audit run something of theirs.", 42, 0),
    (13, "reddit", 10, "The vendor lock-in complaints are fair but people forget WHY. Nobody else wanted to maintain the regulatory layer for a market of 38 million people.", 108, 4),
    (22, "twitter", 10, "Q4 numbers were fine, not exciting. The Israeli segment is carrying more than the market seems to price in.", 48, 1),
    (18, "twitter", 10, "Their conference presence is dramatically better than three years ago. Whoever runs brand now is doing something right.", 53, 2),
    (2, "twitter", 11, "Also the licensing model. Per user, per module, per environment, per phase of the moon.", 94, 3),
    (7, "reddit", 11, "As a hospital IT lead I will say the thing nobody says: their software is ugly and it works. I would take that trade every single time.", 137, 6),
    (15, "reddit", 11, "Ugly and works is fine until you need to hire someone under 30 to maintain it.", 121, 7),
    (4, "twitter", 12, "Procurement dependency is the structural risk here. When most revenue comes from public contracts, a change of government is a roadmap event.", 89, 4),
    (14, "reddit", 12, "Six years in. Two internal moves, both approved without drama. That flexibility is rarer than people think.", 66, 2),
    (23, "twitter", 12, "Genuine question, not snark: what is the flagship product? Every large vendor has one. I cannot name theirs.", 92, 9),
    (11, "twitter", 13, "If the cloud roadmap were credible I would shortlist them tomorrow. Domain knowledge is genuinely best in market.", 71, 1),
    (8, "twitter", 13, "Second ticket, seven days, still open. Posting this so it gets seen.", 104, 2),
    (19, "twitter", 13, "The R&D spend is real and it mostly goes into things customers never see. Compliance engines, audit trails, migration tooling. Unglamorous and necessary.", 62, 3),
    (17, "reddit", 14, "Recruiter here internally - the salary criticism landed. Bands were revised in January. Still not FAANG, but the gap is much smaller than this thread suggests.", 118, 5),
    (20, "twitter", 14, "Brand campaign is well made. It also carefully avoids saying anything about cloud, AI or the developer experience. That is a choice.", 86, 4),
    (0, "twitter", 14, "We renewed. Not because it was exciting, but because the switching cost analysis was brutal and their people know our estate better than we do.", 73, 2),
    (6, "twitter", 15, "Third project with them. Same lead architect all three times. Continuity of people is underrated in public sector work.", 58, 1),
    (24, "twitter", 15, "Every reply defending them is about reliability. Not one is about innovation. That tells you the positioning.", 111, 8),
    (21, "twitter", 15, "Reliability IS the positioning. Not every company needs to be a growth story.", 69, 3),
]

COMMENTS = [
    (13, "reddit", 3, 3, "This is the most accurate description of enterprise software I have read all year.", 44, 0),
    (15, "reddit", 3, 5, "Same experience. The codebase is genuinely important and genuinely painful.", 38, 1),
    (14, "reddit", 3, 6, "Depends heavily on the unit. Healthcare side has modern tooling now.", 29, 2),
    (2, "twitter", 2, 1, "Five years is optimistic. We scoped seven.", 51, 1),
    (23, "twitter", 2, 4, "Vendor lock-in is the actual product here, the software is the delivery mechanism.", 63, 6),
    (1, "twitter", 2, 7, "Or you could call it 'a system nobody has needed to replace'.", 47, 3),
    (16, "reddit", 4, 8, "How long did the offer take? Mine has been three weeks in 'final approval'.", 22, 0),
    (17, "reddit", 4, 9, "Hiring process is slow, that is a fair criticism and everyone internal knows it.", 35, 0),
    (4, "twitter", 3, 11, "Eleven months is not unusual, that is the part that should worry people.", 58, 1),
    (5, "twitter", 3, 12, "In fairness the change request process is written into the tender, not invented by the vendor.", 41, 2),
    (7, "twitter", 4, 14, "Ours held through the same period. Whatever they do on the security side, it works.", 52, 0),
    (8, "twitter", 4, 15, "Security is solid. Support is what has slipped.", 46, 1),
    (11, "twitter", 5, 17, "This. Domain depth is unmatched, cloud posture is a decade behind.", 55, 1),
    (10, "twitter", 5, 18, "Agreed on integration effort, we saw the same overrun. Budget double whatever they scope.", 49, 0),
    (15, "reddit", 5, 20, "The raise gap is the single biggest reason people leave. It is not the tech.", 72, 3),
    (14, "reddit", 5, 21, "Bands moved in January though. Worth rechecking before writing them off.", 40, 2),
    (13, "reddit", 5, 22, "Still below market for senior. Better, but below.", 57, 1),
    (8, "twitter", 6, 24, "Nine days on a clinical system should be an incident, not a ticket.", 63, 0),
    (9, "twitter", 6, 25, "Ours was four days last month. Inconsistent rather than uniformly bad.", 28, 1),
    (6, "twitter", 6, 26, "On-site presence is genuinely the differentiator in public sector work.", 33, 0),
    (20, "twitter", 6, 27, "Overlapping product lines is the price of growth by acquisition.", 44, 2),
    (18, "twitter", 6, 28, "It is coherent internally, just not externally communicated. Different problem.", 31, 1),
    (17, "reddit", 7, 30, "This is why I stayed. My work is boring and it matters.", 48, 1),
    (16, "reddit", 7, 31, "Genuinely the most persuasive argument in this whole thread.", 36, 0),
    (24, "twitter", 7, 33, "Legacy is not an insult, it just means nobody can afford to replace it.", 67, 5),
    (1, "twitter", 7, 34, "Correct, and that is called revenue.", 58, 4),
    (21, "twitter", 8, 36, "Low churn is exactly why the multiple holds up.", 30, 0),
    (22, "twitter", 8, 37, "Watch the public sector concentration though, that is the real risk line.", 39, 1),
    (3, "twitter", 9, 39, "The documentation problem is real. Nobody reads 400 pages, so nobody configures it correctly.", 54, 0),
    (0, "twitter", 9, 40, "We paid for a two week enablement instead. Solved it, but it should not have been necessary.", 43, 1),
    (12, "twitter", 9, 41, "Catching errors in the customer spec is the mark of a team that has done it before.", 37, 0),
    (11, "twitter", 9, 42, "Credit where due, their architects push back properly instead of just billing hours.", 45, 1),
    (2, "twitter", 11, 44, "The licensing model deserves its own support ticket.", 71, 2),
    (23, "twitter", 11, 45, "Per environment pricing in 2026 is genuinely wild.", 62, 4),
    (0, "twitter", 11, 46, "We negotiated it down substantially. It is a starting position, not a fixed price.", 35, 1),
    (15, "reddit", 11, 47, "Hiring under 30s for COBOL adjacent work is the actual existential problem.", 88, 3),
    (13, "reddit", 11, 48, "They know. That is what the internal Java modernisation programme is for.", 52, 1),
    (14, "reddit", 11, 49, "That programme is real and it is moving faster than people outside think.", 41, 2),
    (4, "twitter", 12, 51, "Government change as a roadmap event is the most honest sentence in this thread.", 66, 1),
    (5, "twitter", 12, 52, "Every vendor in this market has that exposure, not just them.", 38, 2),
    (23, "twitter", 12, 53, "Nobody has answered my flagship product question, which is itself the answer.", 74, 6),
    (19, "twitter", 12, 54, "The flagship is the install base. That is a legitimate strategy, just not a marketable one.", 59, 2),
    (8, "twitter", 13, 56, "Update: ticket closed on day nine with 'working as designed'. Excellent.", 97, 1),
    (9, "twitter", 13, 57, "That response template needs to die.", 51, 0),
    (11, "twitter", 13, 58, "If they ship a credible managed cloud offer they win half this thread back.", 47, 1),
    (17, "reddit", 14, 60, "Band revision was real, I saw the letters go out. Not universal though - depends on unit.", 55, 2),
    (15, "reddit", 14, 61, "'Depends on unit' is how you keep the gap alive.", 63, 1),
    (20, "twitter", 14, 63, "The campaign avoiding AI entirely is either discipline or absence. Genuinely cannot tell.", 58, 3),
    (18, "twitter", 14, 64, "Discipline, I think. They ship AI in products without branding it as such.", 42, 1),
    (0, "twitter", 14, 65, "Switching cost analysis is the whole enterprise software industry in three words.", 61, 0),
    (6, "twitter", 15, 67, "Continuity of people is the single most underrated procurement criterion.", 39, 0),
    (24, "twitter", 15, 68, "Reliability as positioning works right up until a competitor is reliable AND modern.", 83, 4),
    (21, "twitter", 15, 69, "Name one in this segment that is both, at this scale, under these regulators.", 57, 3),
    (24, "twitter", 15, 70, "Give it three years.", 66, 5),
]


def _agent(agent_id):
    for a in ALL_AGENTS:
        if a[0] == agent_id:
            return a
    raise KeyError(agent_id)


# The like counts above were written for readability, on the scale of a public
# feed. A 25-agent simulation cannot produce 167 likes, so they are compressed
# into a range the audience can actually generate. Relative ranking - which is
# what "top liked post" depends on - is preserved.
_MAX_RAW_LIKES = max(
    max(p[4] for p in POSTS),
    max(c[5] for c in COMMENTS),
)
_LIKE_CEILING = len(ALL_AGENTS) - 2


def _scale_likes(raw):
    """Map a headline like count onto what this audience size can produce."""
    if raw <= 0:
        return 0
    return max(1, round(raw / _MAX_RAW_LIKES * _LIKE_CEILING))


def _scale_dislikes(raw):
    if raw <= 0:
        return 0
    return max(1, round(raw / _MAX_RAW_LIKES * _LIKE_CEILING * 0.6))


def _reset_dir():
    if os.path.exists(SIM_DIR):
        shutil.rmtree(SIM_DIR)
    os.makedirs(os.path.join(SIM_DIR, "twitter"), exist_ok=True)
    os.makedirs(os.path.join(SIM_DIR, "reddit"), exist_ok=True)


def _write_config():
    """The config the prepare step would have produced."""
    agent_configs = []
    for agent_id, name, handle, segment, stance in ALL_AGENTS:
        agent_configs.append({
            "agent_id": agent_id,
            "entity_name": name,
            "entity_type": segment,
            "stance": stance,
            "activity_level": round(random.uniform(0.35, 0.9), 2),
            "sentiment_bias": {"supportive": 0.5, "opposing": -0.5, "neutral": 0.0, "observer": 0.0}[stance],
            "influence_weight": round(random.uniform(0.4, 1.0), 2),
            "posts_per_hour": random.randint(0, 2),
            "comments_per_hour": random.randint(0, 3),
            "response_delay_min": 5,
            "response_delay_max": 45,
            "active_hours": sorted(random.sample(range(7, 24), 11)),
        })

    config = {
        "time_config": {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,
            "agents_per_hour_min": 4,
            "agents_per_hour_max": 18,
            "peak_hours": [9, 10, 11, 13, 14, 20, 21],
            "off_peak_hours": [0, 1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "reasoning": "B2B audience: weekday working hours dominate, small evening tail.",
        },
        "event_config": {
            "initial_posts": [
                {"content": text, "poster_agent_id": idx}
                for idx, text in enumerate(SEED_POSTS)
            ],
        },
        "agent_configs": agent_configs,
        "platform_configs": {
            "twitter": {"platform": "twitter", "recency_weight": 0.4},
            "reddit": {"platform": "reddit", "recency_weight": 0.3},
        },
    }

    with open(os.path.join(SIM_DIR, "simulation_config.json"), 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _build_platform_db(platform):
    """Write one platform database in the shape OASIS leaves behind."""
    db_path = os.path.join(SIM_DIR, f"{platform}_simulation.db")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("CREATE TABLE user (user_id INTEGER PRIMARY KEY, user_name TEXT, name TEXT, bio TEXT)")
    c.execute("""CREATE TABLE post (post_id INTEGER PRIMARY KEY, user_id INTEGER,
                 original_post_id INTEGER, content TEXT, quote_content TEXT,
                 created_at TEXT, num_likes INTEGER, num_dislikes INTEGER,
                 num_shares INTEGER, num_reports INTEGER)""")
    c.execute("""CREATE TABLE comment (comment_id INTEGER PRIMARY KEY, post_id INTEGER,
                 user_id INTEGER, content TEXT, created_at TEXT,
                 num_likes INTEGER, num_dislikes INTEGER)""")

    for agent_id, name, handle, segment, _ in ALL_AGENTS:
        c.execute("INSERT INTO user VALUES (?,?,?,?)",
                  (agent_id, handle, name, SEGMENTS[segment]))

    base = datetime(2026, 3, 2, 8, 0, 0)
    post_id = 1

    # The campaign creative goes in first, exactly as the runner seeds it.
    for idx, text in enumerate(SEED_POSTS):
        c.execute("INSERT INTO post VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (post_id, idx, None, text, None, base.isoformat(),
                   random.randint(6, 14), random.randint(0, 3), random.randint(2, 6), 0))
        post_id += 1

    # Audience posts for this platform
    post_ids = {}
    for agent_id, plat, rnd, text, likes, dislikes in POSTS:
        if plat != platform:
            continue
        created = base + timedelta(hours=rnd)
        scaled_likes = _scale_likes(likes)
        c.execute("INSERT INTO post VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (post_id, agent_id, None, text, None, created.isoformat(),
                   scaled_likes, _scale_dislikes(dislikes), scaled_likes // 4, 0))
        post_ids[len(post_ids)] = post_id
        post_id += 1

    available = list(post_ids.values())
    comment_id = 1
    for agent_id, plat, rnd, _slot, text, likes, dislikes in (
        (a, p, r, s, t, l, d) for a, p, r, s, t, l, d in COMMENTS
    ):
        if plat != platform or not available:
            continue
        created = base + timedelta(hours=rnd, minutes=random.randint(5, 55))
        parent = available[comment_id % len(available)]
        c.execute("INSERT INTO comment VALUES (?,?,?,?,?,?,?)",
                  (comment_id, parent, agent_id, text, created.isoformat(),
                   _scale_likes(likes), _scale_dislikes(dislikes)))
        comment_id += 1

    conn.commit()
    conn.close()
    return post_id - 1, comment_id - 1


def _write_action_logs():
    """
    Write the action log the KPI panel counts from.

    Every authored post and comment gets a CREATE_* record, and the like and
    dislike counts are expanded back into individual reaction records so reach,
    engagement and sentiment come out consistent with the feed.
    """
    base = datetime(2026, 3, 2, 8, 0, 0)
    logs = {"twitter": [], "reddit": []}

    def emit(platform, rnd, agent_id, action_type, args=None):
        name = _agent(agent_id)[1]
        logs[platform].append({
            "round": rnd,
            "timestamp": (base + timedelta(hours=rnd, minutes=random.randint(0, 59))).isoformat(),
            "agent_id": agent_id,
            "agent_name": name,
            "action_type": action_type,
            "action_args": args or {},
            "result": "success",
            "success": True,
        })

    # Passive share is measured per agent, not per action: it is the share of
    # reached agents that engaged with nothing at all. Agents who never author
    # are kept out of the reaction pool too, so the demo has real lurkers
    # instead of a 100% engagement rate no campaign has ever achieved.
    authors = {p[0] for p in POSTS} | {c[0] for c in COMMENTS}
    reactor_ids = [a[0] for a in ALL_AGENTS if a[0] in authors]
    lurker_ids = [a[0] for a in ALL_AGENTS if a[0] not in authors]

    def reactors(author_id, count):
        """Distinct agents other than the author - nobody likes their own post."""
        pool = [a for a in reactor_ids if a != author_id]
        return random.sample(pool, k=min(count, len(pool)))

    for agent_id, platform, rnd, text, likes, dislikes in POSTS:
        emit(platform, rnd, agent_id, "CREATE_POST", {"content": text})
        for rid in reactors(agent_id, _scale_likes(likes)):
            emit(platform, rnd, rid, "LIKE_POST",
                 {"post_content": text[:120], "post_author_name": _agent(agent_id)[1]})
        for rid in reactors(agent_id, _scale_dislikes(dislikes)):
            emit(platform, rnd, rid, "DISLIKE_POST",
                 {"post_content": text[:120], "post_author_name": _agent(agent_id)[1]})
        # Only the posts that really landed get amplified.
        if likes >= 100:
            for rid in reactors(agent_id, random.randint(2, 4)):
                emit(platform, rnd, rid, "REPOST", {"post_content": text[:120]})

    for agent_id, platform, rnd, _slot, text, likes, dislikes in COMMENTS:
        emit(platform, rnd, agent_id, "CREATE_COMMENT", {"content": text})
        for rid in reactors(agent_id, _scale_likes(likes)):
            emit(platform, rnd, rid, "LIKE_COMMENT", {"comment_content": text[:120]})
        for rid in reactors(agent_id, _scale_dislikes(dislikes)):
            emit(platform, rnd, rid, "DISLIKE_COMMENT", {"comment_content": text[:120]})

    # Agents who were reached but scrolled past.
    all_ids = [a[0] for a in ALL_AGENTS]
    for rnd in range(1, 16):
        for agent_id in random.sample(all_ids, k=random.randint(9, 16)):
            emit(random.choice(("twitter", "reddit")), rnd, agent_id, "DO_NOTHING")

    # Every lurker must appear at least once, or they are not "reached" at all
    # and drop out of the denominator instead of counting as passive.
    for agent_id in lurker_ids:
        for rnd in random.sample(range(1, 16), k=3):
            emit(random.choice(("twitter", "reddit")), rnd, agent_id, "DO_NOTHING")

    for platform, entries in logs.items():
        entries.sort(key=lambda e: e["timestamp"])
        path = os.path.join(SIM_DIR, platform, "actions.jsonl")
        with open(path, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return {p: len(e) for p, e in logs.items()}


def _write_states():
    """Mark the run finished, so the UI treats the demo as complete."""
    now = datetime.now().isoformat()

    run_state = {
        "simulation_id": SIM_ID,
        "runner_status": "completed",
        "current_round": 16,
        "total_rounds": 16,
        "simulated_hours": 16,
        "total_simulation_hours": 72,
        "twitter_current_round": 16,
        "reddit_current_round": 16,
        "started_at": now,
        "finished_at": now,
    }
    with open(os.path.join(SIM_DIR, "run_state.json"), 'w', encoding='utf-8') as f:
        json.dump(run_state, f, ensure_ascii=False, indent=2)

    state = {
        "simulation_id": SIM_ID,
        "project_id": PROJECT_ID,
        "graph_id": GRAPH_ID,
        "status": "completed",
        "created_at": now,
        "updated_at": now,
        "entities_count": len(ALL_AGENTS),
        "entity_types": sorted(SEGMENTS.keys()),
        "enable_twitter": True,
        "enable_reddit": True,
    }
    with open(os.path.join(SIM_DIR, "state.json"), 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _write_profiles():
    """Persona files, so the interview tool has something to select from."""
    import csv

    twitter_path = os.path.join(SIM_DIR, "twitter_profiles.csv")
    with open(twitter_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "name", "username", "description", "following_count", "followers_count"])
        for agent_id, name, handle, segment, stance in ALL_AGENTS:
            writer.writerow([
                agent_id, name, handle,
                f"{SEGMENTS[segment]}. Prior stance toward the brand: {stance}.",
                random.randint(80, 900), random.randint(120, 4000),
            ])

    reddit_path = os.path.join(SIM_DIR, "reddit_profiles.json")
    profiles = [{
        "user_id": agent_id,
        "name": name,
        "username": handle,
        "bio": f"{SEGMENTS[segment]}. Prior stance toward the brand: {stance}.",
        "persona": f"{name} is a {SEGMENTS[segment].lower()} evaluating Asseco. Prior stance: {stance}.",
        "source_entity_type": segment,
    } for agent_id, name, handle, segment, stance in ALL_AGENTS]
    with open(reddit_path, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


def _write_project():
    """A project record, so the campaign brief reaches the classifier."""
    project = ProjectManager.create_project(name="Asseco Group - brand campaign (demo)")
    # create_project mints its own id; move it onto the demo id the sim expects.
    old_dir = ProjectManager._get_project_dir(project.project_id)
    new_dir = ProjectManager._get_project_dir(PROJECT_ID)
    if os.path.exists(new_dir):
        shutil.rmtree(new_dir)
    os.rename(old_dir, new_dir)

    project.project_id = PROJECT_ID
    project.graph_id = GRAPH_ID
    project.status = ProjectStatus.COMPLETED if hasattr(ProjectStatus, 'COMPLETED') else project.status
    project.simulation_requirement = CAMPAIGN_BRIEF
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(PROJECT_ID, CAMPAIGN_BRIEF)


def seed():
    random.seed(20260302)  # Reproducible demo numbers

    _reset_dir()
    _write_config()
    tw_posts, tw_comments = _build_platform_db("twitter")
    rd_posts, rd_comments = _build_platform_db("reddit")
    counts = _write_action_logs()
    _write_states()
    _write_profiles()
    try:
        _write_project()
        project_note = f"project {PROJECT_ID} (campaign brief attached)"
    except Exception as e:
        project_note = f"project record skipped ({type(e).__name__}: {e})"

    print(f"Seeded {SIM_ID}")
    print(f"  agents          : {len(ALL_AGENTS)} ({len(AGENTS)} vocal, {len(LURKERS)} passive) across {len(SEGMENTS)} segments")
    print(f"  twitter         : {tw_posts} posts / {tw_comments} comments")
    print(f"  reddit          : {rd_posts} posts / {rd_comments} comments")
    print(f"  seed creative   : {len(SEED_POSTS)} posts (excluded from sentiment)")
    print(f"  action log      : {counts['twitter']} twitter / {counts['reddit']} reddit records")
    print(f"  {project_note}")
    print(f"  path            : {os.path.abspath(SIM_DIR)}")
    print()
    print(f"  GET /api/simulation/{SIM_ID}/sentiment-digest")
    print(f"  GET /api/simulation/{SIM_ID}/campaign-metrics")


def delete():
    if os.path.exists(SIM_DIR):
        shutil.rmtree(SIM_DIR)
        print(f"removed {SIM_DIR}")
    project_dir = ProjectManager._get_project_dir(PROJECT_ID)
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
        print(f"removed {project_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delete', action='store_true', help="remove the demo simulation")
    args = parser.parse_args()

    if args.delete:
        delete()
    else:
        seed()
