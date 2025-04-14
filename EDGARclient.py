import asyncio
import json
import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import logging
import re
from difflib import get_close_matches
from dotenv import load_dotenv
import os

load_dotenv('auth.env')

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Supported database keys
DATABASES = ["n2o", "fluorinated", "co2", "ch4"]

# Emission metrics mapping to database keys and substances
EMISSION_METRICS = {
    "n2o_emissions": {"db_key": "n2o", "substance": "N2O", "aliases": ["n2o", "nitrous oxide"]},
    "fluorinated_emissions": {"db_key": "fluorinated", "substance": None, "aliases": ["fluorinated", "hfcs", "pfcs", "sf6", "nf3"]},
    "co2_emissions": {"db_key": "co2", "substance": "CO2", "aliases": ["co2", "carbon dioxide"]},
    "ch4_emissions": {"db_key": "ch4", "substance": "CH4", "aliases": ["ch4", "methane"]}
}

# Units for metrics
METRIC_UNITS = {
    "n2o_emissions": "kt N₂O",
    "fluorinated_emissions": "kt CO₂eq",
    "co2_emissions": "Mt CO₂",
    "ch4_emissions": "Mt CH₄"
}

# Fluorinated gas substances
FLUORINATED_SUBSTANCES = [
    "HFC-125", "HFC-134a", "HFC-143a", "HFC-152a", "HFC-227ea", "HFC-245fa",
    "HFC-32", "HFC-365mfc", "HCFC-141b", "HCFC-142b", "C2F6", "CF4", "SF6",
    "C3F8", "C4F10", "HFC-23", "HFC-134", "HFC-236fa", "HFC-43-10-mee",
    "c-C4F8", "NF3", "C5F12", "C6F14", "HFC-143", "HFC-41"
]

# HFC substances
HFC_SUBSTANCES = [s for s in FLUORINATED_SUBSTANCES if s.startswith("HFC-")]

# Words to exclude from country name extraction
EXCLUDED_WORDS = {
    "emissions", "in", "the", "what", "is", "of", "for", "at", "on",
    "hello", "hi", "hey", "compare", "and", "vs", "versus", "total", "all",
    "ch4", "co2", "n2o", "fluorinated", "hfc", "methane", "carbon", "dioxide",
    "nitrous", "oxide", "hfcs", "pfcs", "sf6", "nf3"
} | set(FLUORINATED_SUBSTANCES)

async def validate_schemas(session):
    """Validate database schemas at startup."""
    for db_key in DATABASES:
        try:
            result = await session.call_tool(
                "query_data",
                arguments={"db_key": db_key, "sql": "SELECT sql FROM sqlite_master WHERE type='table'"}
            )
            if result.content and result.content[0].text:
                logger.info(f"Schema for {db_key}: {result.content[0].text}")
            else:
                logger.warning(f"No schema found for {db_key}")
        except Exception as e:
            logger.error(f"Error fetching schema for {db_key}: {str(e)}")

async def extract_countries(session, question, db_key, substance=None):
    """Extract country names from the question, validated against the database."""
    question_lower = question.lower()
    logger.debug(f"Extracting countries from: {question_lower}")
    
    # Get valid countries
    args = {"db_key": db_key}
    if substance:
        args["substance"] = substance
    
    result_names = await session.call_tool("get_country_names", arguments=args)
    valid_countries = []
    if result_names.content:
        valid_countries = [content.text.strip() for content in result_names.content if content.text.strip()]
        logger.debug(f"Valid countries for {db_key} (substance: {substance}): {valid_countries}")
    else:
        logger.error(f"Failed to fetch country names for {db_key}")
        return ["unknown"]

    # Query country codes
    result_codes = await session.call_tool(
        "query_data",
        arguments={
            "db_key": db_key,
            "sql": "SELECT Name, Country_code_A3 FROM emissions GROUP BY Name, Country_code_A3"
        }
    )
    country_code_map = {}
    valid_codes = []
    if result_codes.content:
        logger.debug(f"Raw country codes result: {[content.text for content in result_codes.content]}")
        for content in result_codes.content:
            if content.text:
                try:
                    text = content.text.strip()
                    if text.startswith("(") and text.endswith(")"):
                        text = text[1:-1]
                        name, code = [x.strip().strip("'") for x in text.split(",", 1)]
                        if name and code:
                            country_code_map[code.lower()] = name
                            valid_codes.append(code)
                            logger.debug(f"Parsed: {code} -> {name}")
                except Exception as e:
                    logger.error(f"Error parsing code: {content.text}, {str(e)}")
        logger.info(f"Country code map for {db_key}: {country_code_map}")
    else:
        logger.warning(f"No country codes found for {db_key}")

    # Extract candidates
    phrases = re.split(r'\band\b|\bvs\b|\bversus\b|,', question_lower)
    candidates = []
    for phrase in phrases:
        words = phrase.strip().split()
        for i in range(len(words)):
            for j in range(i + 1, len(words) + 1):
                candidate = " ".join(words[i:j])
                if candidate not in EXCLUDED_WORDS:
                    candidates.append(candidate)
    
    logger.debug(f"Country candidates: {candidates}")
    countries = []
    
    # Match candidates
    for candidate in candidates:
        candidate_lower = candidate.lower()
        # Check country codes first
        if candidate_lower in country_code_map:
            full_name = country_code_map[candidate_lower]
            countries.append(full_name)
            logger.info(f"Exact match (code): '{candidate}' -> '{full_name}'")
            continue
        
        # Check country names
        for country in valid_countries:
            if candidate_lower == country.lower():
                countries.append(country)
                logger.info(f"Exact match (name): '{candidate}' -> '{country}'")
                break
        else:
            # Fuzzy match on country names only (codes are exact)
            matches = get_close_matches(candidate_lower, [c.lower() for c in valid_countries], n=1, cutoff=0.85)
            if matches:
                # Find the original case-sensitive country name
                for country in valid_countries:
                    if country.lower() == matches[0]:
                        countries.append(country)
                        logger.info(f"Fuzzy match (name): '{candidate}' -> '{country}'")
                        break
            else:
                logger.debug(f"No match for: '{candidate}'")

    # Remove duplicates
    seen = set()
    countries = [c for c in countries if not (c.lower() in seen or seen.add(c.lower()))]
    
    logger.debug(f"Final countries: {countries}")
    return countries if countries else ["unknown"]

async def extract_fluorinated_substance(question):
    """Extract specific fluorinated substance or group."""
    question_lower = question.lower()
    
    if "hfc" in question_lower and not any(s.lower() in question_lower for s in FLUORINATED_SUBSTANCES):
        logger.info("Detected substance group: 'HFC'")
        return "HFC"
    
    for substance in FLUORINATED_SUBSTANCES:
        if substance.lower() in question_lower:
            logger.info(f"Detected substance: '{substance}'")
            return substance
    
    return None

async def generate_query(question, session):
    """Generate SQL queries based on the question."""
    question_lower = question.lower().strip()
    logger.info(f"Processing question: '{question_lower}'")

    # Detect metrics
    metrics = []
    for metric_key, info in EMISSION_METRICS.items():
        patterns = [metric_key.replace("_", " ")] + info["aliases"]
        for pattern in patterns:
            if pattern.lower() in question_lower:
                metrics.append(metric_key)
                logger.info(f"Metric found: '{metric_key}' via '{pattern}'")
                break
    
    if not metrics:
        logger.error("No emission metric found")
        return None

    # Extract years
    years = []
    for word in re.findall(r'\b\d{4}\b', question_lower):
        if 1970 <= int(word) <= 2023:
            years.append(word)
    
    if not years:
        logger.error("No valid year found (1970-2023)")
        return None
    logger.info(f"Years: {years}")

    queries = []
    for metric in metrics:
        db_key = EMISSION_METRICS[metric]["db_key"]
        default_substance = EMISSION_METRICS[metric]["substance"]

        substance_for_countries = default_substance
        if metric == "fluorinated_emissions":
            substance_for_countries = None
        countries = await extract_countries(session, question, db_key, substance_for_countries)
        if countries == ["unknown"]:
            logger.error(f"No country found for {metric}")
            continue
        logger.info(f"Countries for {metric}: {countries}")

        for country in countries:
            for year in years:
                safe_country = country.replace("'", "''")
                if metric == "fluorinated_emissions":
                    specific_substance = await extract_fluorinated_substance(question)
                    if specific_substance == "HFC":
                        hfc_substances = ", ".join(f"'{s}'" for s in HFC_SUBSTANCES)
                        query = (
                            f"SELECT SUM(\"{year}\") FROM emissions "
                            f"WHERE Name = '{safe_country}' AND Substance IN ({hfc_substances})"
                        )
                        queries.append({
                            "db_key": db_key,
                            "query": query,
                            "metric": metric,
                            "country": country,
                            "year": year,
                            "substance": "all HFC gases"
                        })
                        logger.info(f"Query: {query}")
                    elif specific_substance:
                        query = (
                            f"SELECT \"{year}\" FROM emissions "
                            f"WHERE Name = '{safe_country}' AND Substance = '{specific_substance}'"
                        )
                        queries.append({
                            "db_key": db_key,
                            "query": query,
                            "metric": metric,
                            "country": country,
                            "year": year,
                            "substance": specific_substance
                        })
                        logger.info(f"Query: {query}")
                    else:
                        for substance in FLUORINATED_SUBSTANCES:
                            query = (
                                f"SELECT \"{year}\" FROM emissions "
                                f"WHERE Name = '{safe_country}' AND Substance = '{substance}'"
                            )
                            queries.append({
                                "db_key": db_key,
                                "query": query,
                                "metric": metric,
                                "country": country,
                                "year": year,
                                "substance": substance
                            })
                            logger.info(f"Query: {query}")
                        query = (
                            f"SELECT SUM(\"{year}\") FROM emissions "
                            f"WHERE Name = '{safe_country}'"
                        )
                        queries.append({
                            "db_key": db_key,
                            "query": query,
                            "metric": metric,
                            "country": country,
                            "year": year,
                            "substance": "all fluorinated gases"
                        })
                        logger.info(f"Query: {query}")
                else:
                    query = (
                        f"SELECT \"{year}\" FROM emissions "
                        f"WHERE Name = '{safe_country}' AND Substance = '{default_substance}'"
                    )
                    queries.append({
                        "db_key": db_key,
                        "query": query,
                        "metric": metric,
                        "country": country,
                        "year": year,
                        "substance": default_substance
                    })
                    logger.info(f"Query: {query}")

    return queries if queries else None

def call_climategpt(question, data_list):
    """Generate a natural response using ClimateGPT."""
    url = "https://erasmus.ai/models/climategpt_8b_latest/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    # Load credentials from environment variables
    auth = (os.getenv("API_USER"), os.getenv("API_KEY"))
    
    if not data_list:
        prompt = f"No data found for: '{question}'."
    else:
        response_data = []
        total_sums = {}
        metric_groups = {}
        
        for data, query_info in data_list:
            metric = query_info['metric']
            if metric not in metric_groups:
                metric_groups[metric] = []
            metric_groups[metric].append((data, query_info))
        
        for metric, group in metric_groups.items():
            for data, query_info in group:
                country = query_info['country']
                year = query_info['year']
                substance = query_info['substance']
                key = (country, year, metric)
                
                if not data or data == "No data found for the query.":
                    response_data.append(
                        f"No data for {country} in {year} for {substance} ({metric})"
                    )
                else:
                    try:
                        value = float(data.split(",")[0].strip("()")) if data else None
                        unit = METRIC_UNITS.get(metric, "unknown unit")
                        if substance == "all fluorinated gases":
                            total_sums[key] = value
                        else:
                            response_data.append(
                                f"{country} in {year}: {substance} ({metric}) = {value} {unit}"
                            )
                    except (ValueError, IndexError) as e:
                        logger.error(f"Error parsing {country} {year} {substance}: {str(e)}")
                        response_data.append(
                            f"Error parsing data for {country} in {year} ({substance}, {metric})"
                        )
            
            for (country, year, m), total in total_sums.items():
                if m == metric:
                    unit = METRIC_UNITS.get(metric, "unknown unit")
                    response_data.append(
                        f"Total for {country} in {year}: all fluorinated gases ({metric}) = {total} {unit}"
                    )
        
        response_data.sort(key=lambda x: (
            x.split(" in ")[0] if " in " in x else "",
            x.split(" in ")[1].split(":")[0] if " in " in x and ":" in x else "",
            x.split(": ")[1].split(" ")[0] if ": " in x else ""
        ))
        prompt = f"Question: {question}\nData:\n" + "\n".join(response_data)

    payload = {
        "model": "/cache/climategpt_8b_latest",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a climate data assistant. Respond based on the provided data, using tables or "
                    "bullet points for clarity, especially for comparisons. If data is missing, note it clearly."
                    "Make the response informative with a friendly tone, and avoid unnecessary details."
                )
            },
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, auth=auth, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"ClimateGPT failed: {str(e)}")
        return "\n".join([f"{query['country']} {query['year']} {query['substance']}: {data}" for data, query in data_list]) or f"No data for '{question}'"

async def process_question(user_input, session):
    """Process user input and return response."""
    logger.info(f"Processing: {user_input}")
    query_info = await generate_query(user_input, session)
    if query_info is None:
        response = call_climategpt(user_input, [])
        return {
            "response": response,
            "queries": [{"db_key": "None", "metric": "None", "country": "None", "year": "None", "substance": "None"}]
        }

    data_list = []
    for query in query_info:
        try:
            result = await session.call_tool(
                "query_data",
                arguments={"db_key": query["db_key"], "sql": query["query"]}
            )
            data = None
            if result.content and result.content[0].text and result.content[0].text != "No data found for the query.":
                data = result.content[0].text
            else:
                logger.warning(f"No data for query: {query['query']}")
            data_list.append((data, query))
            logger.debug(f"Query result for {query['query']}: {data}")
        except Exception as e:
            logger.error(f"Query failed: {query['query']}, {str(e)}")
            data_list.append((None, query))

    response = call_climategpt(user_input, data_list)
    return {
        "response": response,
        "queries": [
            {
                "db_key": q["db_key"],
                "metric": q["metric"],
                "country": q["country"],
                "year": q["year"],
                "substance": q["substance"]
            } for q in query_info
        ]
    }

async def main():
    print("Emissions Chatbot - Type 'exit' to quit")
    server_params = StdioServerParameters(
        command="python",
        args=["emissions_mcp.py"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await validate_schemas(session)

            while True:
                user_input = input("Enter your question: ").strip()
                if user_input.lower() == "exit":
                    print("Goodbye!")
                    break
                try:
                    result = await process_question(user_input, session)
                    for i, query in enumerate(result['queries'], 1):
                        print(f"\nQuery {i}:")
                        print(f"  Database: {query['db_key']}")
                        print(f"  Metric: {query['metric']}")
                        print(f"  Country: {query['country']}")
                        print(f"  Year: {query['year']}")
                        print(f"  Substance: {query['substance']}")
                    print()
                    print("Question:", user_input)
                    print(f"Response: {result['response']}")
                except Exception as e:
                    logger.error(f"Error: {str(e)}")
                    print(f"Error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())