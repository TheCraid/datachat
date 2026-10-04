"""Deterministic synthetic sample datasets.

Every value is generated from a fixed random seed, so the data (and the evaluation's
expected answers) are identical on every machine. No real people or companies.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass
class TableSpec:
    name: str
    description: str
    columns: list[tuple[str, str, str]]  # (name, DuckDB type, description)
    rows: list[tuple] = field(default_factory=list)


@dataclass
class SampleDataset:
    id: str
    title: str
    subtitle: str
    suggestions: list[str]
    tables: list[TableSpec]


FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan", "Kiran",
         "Ananya", "Diya", "Saanvi", "Aadhya", "Kavya", "Meera", "Priya", "Lakshmi", "Nisha", "Pooja",
         "Rahul", "Vikram", "Suresh", "Manoj", "Deepak", "Anil", "Ravi", "Harsha", "Naveen", "Karthik",
         "Divya", "Sneha", "Shreya", "Asha", "Bhavana", "Chitra", "Gayathri", "Keerthi", "Swathi", "Varsha"]
LAST = ["Sharma", "Rao", "Reddy", "Nair", "Iyer", "Patel", "Gowda", "Menon", "Kumar", "Shetty",
        "Joshi", "Das", "Pillai", "Hegde", "Kulkarni", "Naidu", "Bhat", "Verma", "Singh", "Mehta"]

CITIES = {  # city -> state
    "Bengaluru": "Karnataka", "Mysuru": "Karnataka", "Mumbai": "Maharashtra", "Pune": "Maharashtra",
    "Chennai": "Tamil Nadu", "Coimbatore": "Tamil Nadu", "Hyderabad": "Telangana", "Kochi": "Kerala",
    "Delhi": "Delhi", "Kolkata": "West Bengal", "Ahmedabad": "Gujarat", "Jaipur": "Rajasthan",
}


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def _quarter_index(d: date) -> int:
    return (d.year - 2024) * 4 + (d.month - 1) // 3


# --------------------------------------------------------------------------- online store

def online_store() -> SampleDataset:
    rng = random.Random(2026)
    cities = list(CITIES)
    city_weight = {"Bengaluru": 14, "Mumbai": 14, "Delhi": 13, "Hyderabad": 10, "Chennai": 10, "Pune": 9,
                   "Kolkata": 8, "Ahmedabad": 6, "Kochi": 5, "Jaipur": 5, "Coimbatore": 3, "Mysuru": 3}
    # Per-city growth per quarter, so trends differ between cities (Pune slows down in 2026).
    city_trend = {c: rng.uniform(-0.01, 0.06) for c in cities}
    city_trend.update({"Pune": 0.05, "Hyderabad": 0.07, "Kochi": 0.02})

    def city_factor(city: str, d: date) -> float:
        q = _quarter_index(d)
        f = (1 + city_trend[city]) ** q
        if city == "Pune" and d >= date(2026, 7, 1):
            f *= 0.72
        if city == "Chennai" and d >= date(2026, 7, 1):
            f *= 0.88
        return f

    segments = (["Consumer"] * 7) + (["Small Business"] * 2) + ["Corporate"]
    customers = []
    by_city: dict[str, list[int]] = {c: [] for c in cities}
    for cid in range(1, 3001):
        city = rng.choices(cities, weights=[city_weight[c] for c in cities])[0]
        customers.append((cid, _name(rng), city, CITIES[city], _rand_date(rng, date(2023, 6, 1), date(2026, 9, 15)),
                          rng.choice(segments)))
        by_city[city].append(cid)

    catalog = {
        "Electronics": (["Wireless", "Smart", "Portable", "Noise-Cancelling", "Compact"],
                        ["Earbuds", "Speaker", "Power Bank", "Smartwatch", "Keyboard", "Monitor"], (799, 24999)),
        "Fashion": (["Cotton", "Linen", "Denim", "Silk", "Classic"], ["Kurta", "Shirt", "Saree", "Jacket", "Sneakers"],
                    (399, 4999)),
        "Home & Kitchen": (["Steel", "Non-Stick", "Ceramic", "Glass", "Bamboo"],
                           ["Pressure Cooker", "Tawa", "Dinner Set", "Water Bottle", "Mixer Grinder"], (249, 6999)),
        "Books": (["Illustrated", "Pocket", "Collected", "Beginner's", "Advanced"],
                  ["Python Guide", "Novel", "Cookbook", "Atlas", "Poetry Book"], (149, 1299)),
        "Beauty": (["Herbal", "Ayurvedic", "Matte", "Organic", "Daily"],
                   ["Face Wash", "Sunscreen", "Lipstick", "Hair Oil", "Moisturiser"], (99, 1999)),
        "Sports": (["Pro", "Training", "Lightweight", "All-Weather", "Junior"],
                   ["Cricket Bat", "Yoga Mat", "Football", "Badminton Racquet", "Dumbbell Set"], (299, 7999)),
        "Grocery": (["Organic", "Premium", "Family Pack", "Cold-Pressed", "Roasted"],
                    ["Basmati Rice", "Ghee", "Coffee Powder", "Cashews", "Groundnut Oil"], (99, 1499)),
    }
    products = []
    pid = 1
    for category, (adjs, nouns, (lo, hi)) in catalog.items():
        seen = set()
        while len(seen) < 17 + (1 if category == "Electronics" else 0):
            nm = f"{rng.choice(adjs)} {rng.choice(nouns)}"
            if nm in seen:
                continue
            seen.add(nm)
            price = round(rng.uniform(lo, hi) / 10) * 10 - 1
            products.append((pid, nm, category, float(price)))
            pid += 1
    cat_weight = {"Electronics": 1.2, "Fashion": 1.6, "Home & Kitchen": 1.2, "Books": 0.8, "Beauty": 1.1,
                  "Sports": 0.6, "Grocery": 1.5}

    start, end = date(2024, 1, 1), date(2026, 9, 30)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def day_weight(d: date) -> float:
        w = 1 + 0.035 * _quarter_index(d)
        if d.month in (10, 11):
            w *= 1.6   # festive season
        if d.weekday() >= 5:
            w *= 1.15
        return w

    day_w = [day_weight(d) for d in days]
    pay = ["UPI"] * 11 + ["Card"] * 5 + ["Cash on delivery"] * 3 + ["Net banking"]
    orders = []
    for oid in range(100001, 120001):
        d = rng.choices(days, weights=day_w)[0]
        city = rng.choices(cities, weights=[city_weight[c] * city_factor(c, d) for c in cities])[0]
        cust = rng.choice(by_city[city])
        p = rng.choices(products, weights=[cat_weight[x[2]] for x in products])[0]
        qty = rng.choices([1, 2, 3, 4], weights=[70, 20, 7, 3])[0]
        discount = rng.choice([0, 0, 0, 0.05, 0.1, 0.15])
        amount = round(p[3] * qty * (1 - discount), 2)
        age = (end - d).days
        r = rng.random()
        if age < 5 and r < 0.5:
            status = "Shipped"
        elif r < 0.06:
            status = "Cancelled"
        elif r < 0.11:
            status = "Returned"
        else:
            status = "Delivered"
        orders.append((oid, d, cust, p[0], qty, amount, city, status, rng.choice(pay)))

    return SampleDataset(
        id="store",
        title="Online store",
        subtitle="orders · customers · products",
        suggestions=[
            "Which city's sales dropped most last quarter?",
            "Top 5 products by revenue in 2026",
            "Monthly revenue trend for 2026",
            "What share of customers ordered more than once?",
        ],
        tables=[
            TableSpec("orders", "One row per order (Jan 2024 to Sep 2026).", [
                ("order_id", "INTEGER", "Unique order number"),
                ("order_date", "DATE", "Date the order was placed"),
                ("customer_id", "INTEGER", "customers.customer_id"),
                ("product_id", "INTEGER", "products.product_id"),
                ("quantity", "INTEGER", "Units ordered"),
                ("amount", "DECIMAL(12,2)", "Order value in rupees after discount"),
                ("city", "VARCHAR", "Delivery city"),
                ("status", "VARCHAR", "Delivered, Shipped, Cancelled or Returned"),
                ("payment_method", "VARCHAR", "UPI, Card, Cash on delivery or Net banking"),
            ], orders),
            TableSpec("customers", "One row per customer.", [
                ("customer_id", "INTEGER", "Unique customer number"),
                ("name", "VARCHAR", "Customer name (not unique; group by customer_id)"),
                ("city", "VARCHAR", "Home city"),
                ("state", "VARCHAR", "Home state"),
                ("signup_date", "DATE", "Date the customer registered"),
                ("segment", "VARCHAR", "Consumer, Small Business or Corporate"),
            ], customers),
            TableSpec("products", "One row per product.", [
                ("product_id", "INTEGER", "Unique product number"),
                ("name", "VARCHAR", "Product name (group by product_id)"),
                ("category", "VARCHAR", "Product category"),
                ("price", "DECIMAL(10,2)", "List price in rupees"),
            ], products),
        ],
    )


# --------------------------------------------------------------------------- food delivery

def food_delivery() -> SampleDataset:
    rng = random.Random(4242)
    cities = ["Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune", "Kochi"]
    cuisines = {"South Indian": (150, 500), "North Indian": (300, 900), "Biryani": (300, 800),
                "Chinese": (250, 700), "Pizza": (400, 1100), "Cafe": (200, 600), "Desserts": (150, 500)}
    words = ["Spice", "Royal", "Green", "Urban", "Coastal", "Golden", "Little", "Grand", "Hot", "Happy"]
    kinds = {"South Indian": "Tiffins", "North Indian": "Dhaba", "Biryani": "Biryani House", "Chinese": "Wok",
             "Pizza": "Pizzeria", "Cafe": "Cafe", "Desserts": "Sweets"}
    restaurants = []
    for rid in range(1, 151):
        cuisine = rng.choice(list(cuisines))
        city = rng.choice(cities)
        lo, hi = cuisines[cuisine]
        restaurants.append((rid, f"{rng.choice(words)} {kinds[cuisine]}", cuisine, city,
                            round(rng.uniform(3.2, 4.8), 1), rng.random() < 0.35,
                            int(round(rng.uniform(lo, hi) * 2, -1))))
    riders = []
    riders_by_city: dict[str, list[int]] = {c: [] for c in cities}
    vehicles = ["Bike"] * 6 + ["Scooter"] * 3 + ["EV scooter"] * 2 + ["Bicycle"]
    for rid in range(1, 301):
        city = rng.choice(cities)
        joined = _rand_date(rng, date(2022, 1, 1), date(2026, 8, 31))
        riders.append((rid, _name(rng), city, joined, rng.choice(vehicles)))
        riders_by_city[city].append(rid)
    rest_by_city: dict[str, list[tuple]] = {c: [] for c in cities}
    for r in restaurants:
        rest_by_city[r[3]].append(r)

    hour_w = [1, 0.5, 0.3, 0.2, 0.2, 0.3, 0.6, 1.5, 2.5, 2.5, 2, 3, 6, 7, 5, 2.5, 2, 2.5, 3.5, 6, 8, 8, 6, 3]
    start = datetime(2025, 10, 1)
    deliveries = []
    for did in range(1, 18001):
        day = start + timedelta(days=rng.randrange(365))
        hour = rng.choices(range(24), weights=hour_w)[0]
        t = day.replace(hour=hour, minute=rng.randrange(60))
        city = rng.choices(cities, weights=[30, 22, 18, 14, 10, 6])[0]
        rest = rng.choice(rest_by_city[city])
        rider = rng.choice(riders_by_city[city])
        dist = round(rng.uniform(0.6, 11.5), 1)
        base = 12 + dist * 3.2
        if hour in (12, 13, 19, 20, 21):
            base += 6
        if t.month in (6, 7, 8, 9):
            base += 5  # monsoon
        if city in ("Bengaluru", "Mumbai"):
            base += 4
        minutes = max(10, int(rng.gauss(base, 5)))
        value = round(rest[6] / 2 * rng.uniform(0.7, 2.2), 2)
        tip = rng.choice([0, 0, 0, 10, 20, 30, 50])
        r = rng.random()
        status = "Delivered" if r < 0.92 else ("Cancelled" if r < 0.97 else "Failed")
        rating = None
        if status == "Delivered" and rng.random() < 0.7:
            rating = max(1, min(5, round(rng.gauss(5.4 - minutes / 25, 0.8))))
        deliveries.append((did, t, rest[0], rider, city, dist, minutes if status == "Delivered" else None,
                           value, float(tip), status, rating))

    return SampleDataset(
        id="food",
        title="Food delivery",
        subtitle="deliveries · restaurants · riders",
        suggestions=[
            "Average delivery time by city",
            "Which hour of the day has the most orders?",
            "Top 5 restaurants by revenue",
            "Do longer deliveries get lower ratings?",
        ],
        tables=[
            TableSpec("deliveries", "One row per order, Oct 2025 to Sep 2026.", [
                ("delivery_id", "INTEGER", "Unique delivery number"),
                ("order_time", "TIMESTAMP", "When the customer placed the order"),
                ("restaurant_id", "INTEGER", "restaurants.restaurant_id"),
                ("rider_id", "INTEGER", "riders.rider_id"),
                ("city", "VARCHAR", "City of the order"),
                ("distance_km", "DOUBLE", "Restaurant to customer distance"),
                ("delivery_minutes", "INTEGER", "Order to doorstep time; NULL if not delivered"),
                ("order_value", "DECIMAL(10,2)", "Food value in rupees"),
                ("tip", "DECIMAL(8,2)", "Tip in rupees"),
                ("status", "VARCHAR", "Delivered, Cancelled or Failed"),
                ("customer_rating", "INTEGER", "1 to 5; NULL if the customer did not rate"),
            ], deliveries),
            TableSpec("restaurants", "One row per restaurant.", [
                ("restaurant_id", "INTEGER", "Unique restaurant number"),
                ("name", "VARCHAR", "Restaurant name (not unique; group by restaurant_id)"),
                ("cuisine", "VARCHAR", "Main cuisine"),
                ("city", "VARCHAR", "City"),
                ("rating", "DOUBLE", "Average listing rating, 1 to 5"),
                ("is_pure_veg", "BOOLEAN", "True if vegetarian only"),
                ("cost_for_two", "INTEGER", "Typical cost for two in rupees"),
            ], restaurants),
            TableSpec("riders", "One row per delivery rider.", [
                ("rider_id", "INTEGER", "Unique rider number"),
                ("name", "VARCHAR", "Rider name (not unique; group by rider_id)"),
                ("city", "VARCHAR", "City the rider works in"),
                ("joined_date", "DATE", "Date the rider joined"),
                ("vehicle", "VARCHAR", "Bike, Scooter, EV scooter or Bicycle"),
            ], riders),
        ],
    )


# --------------------------------------------------------------------------- HR & payroll

def hr_payroll() -> SampleDataset:
    rng = random.Random(777)
    depts = [  # id, name, location, budget (lakh rupees), titles by level 1..5, base pay at level 1
        (1, "Engineering", "Bengaluru", 1800, ["Software Engineer", "Senior Software Engineer", "Tech Lead",
                                               "Engineering Manager", "Director of Engineering"], 70000),
        (2, "Product", "Bengaluru", 500, ["Associate Product Manager", "Product Manager", "Senior Product Manager",
                                          "Group Product Manager", "Head of Product"], 75000),
        (3, "Sales", "Mumbai", 700, ["Sales Executive", "Account Manager", "Senior Account Manager",
                                     "Regional Sales Manager", "Head of Sales"], 40000),
        (4, "Marketing", "Mumbai", 450, ["Marketing Associate", "Marketing Specialist", "Marketing Manager",
                                         "Senior Marketing Manager", "Head of Marketing"], 42000),
        (5, "Finance", "Pune", 300, ["Accountant", "Financial Analyst", "Finance Manager", "Senior Finance Manager",
                                     "Chief Financial Officer"], 45000),
        (6, "Human Resources", "Pune", 200, ["HR Associate", "HR Generalist", "HR Manager", "Senior HR Manager",
                                             "Head of HR"], 38000),
        (7, "Operations", "Hyderabad", 650, ["Operations Associate", "Operations Analyst", "Operations Manager",
                                             "Senior Operations Manager", "Head of Operations"], 36000),
        (8, "Customer Support", "Hyderabad", 400, ["Support Associate", "Senior Support Associate", "Team Lead",
                                                   "Support Manager", "Head of Support"], 28000),
    ]
    size = {1: 170, 2: 40, 3: 90, 4: 50, 5: 35, 6: 25, 7: 100, 8: 90}
    departments = [(d[0], d[1], d[2], float(d[3])) for d in depts]
    employees = []
    eid = 1001
    heads = {}
    for d in depts:
        did = d[0]
        for i in range(size[did]):
            level = 5 if i == 0 else rng.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]
            hire = _rand_date(rng, date(2016, 1, 1), date(2026, 8, 31))
            exited = rng.random() < (0.22 if did == 8 else 0.12) and hire < date(2026, 1, 1) and level < 5
            exit_date = _rand_date(rng, max(hire + timedelta(days=120), date(2024, 1, 1)), date(2026, 9, 30)) \
                if exited else None
            if exit_date is not None and exit_date <= hire:
                exit_date = None
            emp_type = "Contract" if level == 1 and rng.random() < 0.2 else "Full-time"
            city = d[2] if rng.random() < 0.8 else rng.choice(["Bengaluru", "Mumbai", "Pune", "Hyderabad", "Remote"])
            manager = None if level == 5 else heads.get(did)
            perf = rng.choices([1, 2, 3, 4, 5], weights=[4, 12, 45, 29, 10])[0]
            row = [eid, _name(rng), did, d[4][level - 1], level, hire, emp_type, city,
                   "Exited" if exit_date else "Active", exit_date, manager, perf]
            employees.append(row)
            if level == 5:
                heads[did] = eid
            eid += 1

    base_of = {d[0]: d[5] for d in depts}
    salaries = []
    months = [date(2025 + (m // 12), m % 12 + 1, 1) for m in range(0, 21)]  # Jan 2025 .. Sep 2026
    for e in employees:
        emp_id, did, level, hire, exit_date, perf = e[0], e[2], e[4], e[5], e[9], e[11]
        base = base_of[did] * (1.55 ** (level - 1)) * rng.uniform(0.9, 1.15)
        for m in months:
            if hire > m.replace(day=28) or (exit_date and exit_date < m):
                continue
            pay = base * (1 + (0.04 + 0.02 * (perf - 3)) if m >= date(2026, 4, 1) else 1)
            bonus = round(pay * 0.08 * perf / 3, -2) if m.month in (3, 6, 9, 12) else 0.0
            ded = round(pay * 0.12, 2)
            salaries.append((emp_id, m, round(pay, 2), float(bonus), ded))

    return SampleDataset(
        id="hr",
        title="HR & payroll",
        subtitle="employees · departments · salaries",
        suggestions=[
            "Headcount by department",
            "Average monthly base pay by level",
            "Which department has the highest attrition?",
            "Total payroll cost per month in 2026",
        ],
        tables=[
            TableSpec("employees", "One row per employee, current and former.", [
                ("employee_id", "INTEGER", "Unique employee number"),
                ("name", "VARCHAR", "Employee name (not unique; group by employee_id)"),
                ("department_id", "INTEGER", "departments.department_id"),
                ("job_title", "VARCHAR", "Job title"),
                ("level", "INTEGER", "Seniority level, 1 (junior) to 5 (department head)"),
                ("hire_date", "DATE", "Joining date"),
                ("employment_type", "VARCHAR", "Full-time or Contract"),
                ("city", "VARCHAR", "Work location, or Remote"),
                ("status", "VARCHAR", "Active or Exited"),
                ("exit_date", "DATE", "Last working day; NULL if active"),
                ("manager_id", "INTEGER", "employees.employee_id of the department head; NULL for heads"),
                ("performance_rating", "INTEGER", "Latest rating, 1 to 5"),
            ], [tuple(e) for e in employees]),
            TableSpec("departments", "One row per department.", [
                ("department_id", "INTEGER", "Unique department number"),
                ("name", "VARCHAR", "Department name"),
                ("location", "VARCHAR", "Main office"),
                ("annual_budget_lakh", "DECIMAL(10,2)", "Annual budget in lakh rupees"),
            ], departments),
            TableSpec("salaries", "One row per employee per month paid, Jan 2025 to Sep 2026.", [
                ("employee_id", "INTEGER", "employees.employee_id"),
                ("month", "DATE", "First day of the pay month"),
                ("base_pay", "DECIMAL(12,2)", "Monthly base pay in rupees"),
                ("bonus", "DECIMAL(12,2)", "Quarterly bonus paid that month, else 0"),
                ("deductions", "DECIMAL(12,2)", "Tax and provident fund deductions"),
            ], salaries),
        ],
    )


BUILDERS = {"store": online_store, "food": food_delivery, "hr": hr_payroll}
