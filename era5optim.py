import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import logging
import re
import requests
from difflib import get_close_matches
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from dotenv import load_dotenv
import os

load_dotenv('auth.env')

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simplified schema for the 14 tables
TABLE_SCHEMA = {
    "df0_tables": {
        "columns": ["City", "date", "latitude", "longitude", "high_vegetation_cover", "surface_pressure", "total_ozone", "wind_speed", "skin_temperature"],
        "tables": ["india_df0", "nepal_df0", "bhutan_df0", "pakistan_df0", "bangladesh_df0", "srilanka_df0", "afghanistan_df0"]
    },
    "df1_tables": {
        "columns": ["City", "date", "latitude", "longitude", "uv_radiation", "snowfall", "net_thermal_radiation", "total_precipitation", "convective_rain_rate", "mean_evaporation_rate", "mean_moisture_divergence", "mean_precipitation_rate"],
        "tables": ["india_df1", "nepal_df1", "bhutan_df1", "pakistan_df1", "bangladesh_df1", "srilanka_df1", "afghanistan_df1"]
    }
}

# Metrics to table type mapping
DF0_METRICS = ["total_ozone", "skin_temperature", "high_vegetation_cover", "surface_pressure", "wind_speed"]
DF1_METRICS = ["uv_radiation", "net_thermal_radiation", "convective_rain_rate", "snowfall", "total_precipitation", "mean_evaporation_rate", "mean_moisture_divergence", "mean_precipitation_rate"]
ALL_METRICS = DF0_METRICS + DF1_METRICS

# Units for metrics
METRIC_UNITS = {
    "uv_radiation": "W/m²",
    "net_thermal_radiation": "W/m²",
    "convective_rain_rate": "kg/m²/s",
    "snowfall": "m",
    "total_precipitation": "m",
    "mean_evaporation_rate": "kg/m²/s",
    "mean_moisture_divergence": "kg/m²/s",
    "mean_precipitation_rate": "kg/m²/s",
    "total_ozone": "atm-cm",
    "skin_temperature": "K",
    "high_vegetation_cover": "fraction",
    "surface_pressure": "Pa",
    "wind_speed": "m/s"
}

# Words to exclude from city name extraction
EXCLUDED_WORDS = {"rate", "in", "the", "what", "is", "of", "for", "at", "on", "hello", "hi", "hey", "compare", "and", "vs", "versus"}

MONTHS = ["january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june", "jul", "july", "aug", "august", "sep", "september", "oct", "october", "nov", "november", "dec", "december"]

# Initialize geocoder with country filter
geolocator = Nominatim(user_agent="climate_chatbot")
COUNTRY_CODES = ["PK", "IN", "LK", "NP", "BT", "BD", "AF"]  # Pakistan, India, Sri Lanka, Nepal, Bhutan, Bangladesh, Afghanistan

async def find_tables_with_city(session, city, tables):
    """Search all tables to find which ones contain the city, case-insensitively."""
    matching_tables = []
    for table in tables:
        query = f"SELECT COUNT(*) as count FROM {table} WHERE UPPER(City) = UPPER('{city}')"
        logger.info(f"Checking table {table} with query: {query}")
        result = await session.call_tool(
            "query_table",
            arguments={"table_name": table, "query": query}
        )
        if result.content:
            try:
                data = json.loads(result.content[0].text)
                if data["count"] > 0:
                    matching_tables.append(table)
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error parsing result for {table}: {str(e)}")
                continue
    logger.info(f"Found city '{city}' in tables: {matching_tables}" if matching_tables else f"City '{city}' not found in any table.")
    return matching_tables

async def extract_city_with_geopy(question, question_words, metrics, years, months, session, tables):
    """Extract multiple city names, checking dataset first, then Geopy with fuzzy matching."""
    metric_words = set()
    for metric in metrics:
        metric_words.update(metric.replace("_", " ").split())
    all_excluded = EXCLUDED_WORDS | set(MONTHS) | set(years) | set(months) | metric_words
    
    # Split by commas, "and", or other delimiters to isolate city names
    question_lower = question.lower()
    segments = re.split(r',|\band\b|\bvs\b|\bversus\b', question_lower)
    city_candidates = []
    for segment in segments:
        words = re.findall(r'\b\w+\b', segment.strip())
        for i in range(len(words)):
            for j in range(i + 1, len(words) + 1):
                candidate = " ".join(words[i:j])
                if not any(word in all_excluded for word in words[i:j]) and not re.match(r'(\d{2})[/-](\d{4})', candidate):
                    city_candidates.append(candidate)
    
    if not city_candidates:
        logger.info("No city candidates found after exclusion.")
        return []

    cities = []
    validated_candidates = set()
    
    for candidate in city_candidates:
        if candidate in validated_candidates:
            continue
        
        # Check dataset for exact or close match
        matching_tables = await find_tables_with_city(session, candidate, tables)
        if matching_tables:
            logger.info(f"Dataset validated city: '{candidate}' in tables {matching_tables}")
            cities.append(candidate)
            validated_candidates.add(candidate)
            continue
        
        # Fuzzy match against known cities in dataset
        all_cities = set()
        for table in tables:
            query = f"SELECT DISTINCT City FROM {table}"
            result = await session.call_tool(
                "query_table",
                arguments={"table_name": table, "query": query}
            )
            if result.content:
                try:
                    for content in result.content:
                        data = json.loads(content.text)
                        if isinstance(data, list):
                            all_cities.update(row["City"].lower() for row in data if "City" in row)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Error fetching cities from {table}: {str(e)}")
        
        close_matches = get_close_matches(candidate, all_cities, n=1, cutoff=0.8)
        if close_matches:
            logger.info(f"Fuzzy matched '{candidate}' to '{close_matches[0]}' in dataset")
            cities.append(close_matches[0])
            validated_candidates.add(candidate)
            continue
        
        # Fallback to Geopy
        try:
            location = geolocator.geocode(candidate, country_codes=COUNTRY_CODES, timeout=5)
            if location:
                raw_data = location.raw
                logger.info(f"Geopy check for '{candidate}' returned: {raw_data}")
                if (raw_data.get("addresstype") in ["city", "town", "village", "hamlet"] or 
                    raw_data.get("type") in ["city", "town", "village", "administrative"]):
                    logger.info(f"Geopy validated city: '{candidate}'")
                    cities.append(candidate)
                    validated_candidates.add(candidate)
                    continue
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            logger.error(f"Geopy error for '{candidate}': {str(e)}")
        
        logger.info(f"No match found for '{candidate}' in dataset or Geopy; skipping.")
    
    return cities if cities else []

async def extract_metrics(question_lower):
    """Extract all metrics from the question."""
    question_words = re.findall(r'\b\w+\b', question_lower)
    metric_candidates = [m.replace("_", " ") for m in ALL_METRICS]
    metrics = []
    
    for i in range(len(question_words)):
        for j in range(i + 1, len(question_words) + 1):
            substring = " ".join(question_words[i:j])
            matches = get_close_matches(substring, metric_candidates, n=1, cutoff=0.6)
            if matches and matches[0].replace(" ", "_") in ALL_METRICS:
                metric = matches[0].replace(" ", "_")
                if metric not in metrics:
                    metrics.append(metric)
                    logger.info(f"Found metric: '{metric}'")
    
    return metrics

async def extract_dates(question_lower):
    """Extract all months and years from the question."""
    question_words = re.findall(r'\b\w+\b', question_lower)
    years = []
    months = []
    month_map = {
        "jan": "01", "january": "01", "feb": "02", "february": "02", "mar": "03", "march": "03",
        "apr": "04", "april": "04", "may": "05", "jun": "06", "june": "06", "jul": "07", "july": "07",
        "aug": "08", "august": "08", "sep": "09", "september": "09", "oct": "10", "october": "10",
        "nov": "11", "november": "11", "dec": "12", "december": "12"
    }
    
    for word in question_words:
        if word in month_map:
            months.append(month_map[word])
        elif word.lower() in month_map:
            months.append(month_map[word.lower()])
    
    for word in question_words:
        if re.match(r'(\d{2})[/-](\d{4})', word):
            month_num, year_num = re.split(r'[/-]', word)
            if 1 <= int(month_num) <= 12 and 1900 <= int(year_num) <= 2100:
                months.append(month_num.zfill(2))
                years.append(year_num)
        elif word.isdigit() and len(word) == 4 and 1900 <= int(word) <= 2100:
            years.append(word)
    
    # Default to all months if none specified
    if not months:
        months = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    # Default to a reasonable year if none specified
    if not years:
        years = ["2020"]  # Adjust as needed
    
    logger.info(f"Extracted years: {years}, months: {months}")
    return years, months

async def generate_query(question, tables, session):
    """Generate SQL queries for all metrics, cities, years, and months in the question."""
    question_lower = question.lower().strip()
    logger.info(f"Processing question: '{question_lower}'")

    # Extract components
    metrics = await extract_metrics(question_lower)
    if not metrics:
        logger.error("No recognized metrics found in question.")
        return None
    
    years, months = await extract_dates(question_lower)
    if not years:
        logger.error("Could not extract year from question.")
        return None
    
    question_words = re.findall(r'\b\w+\b', question_lower)
    cities = await extract_city_with_geopy(question_lower, question_words, metrics, years, months, session, tables)
    if not cities:
        logger.error("Could not extract city from question.")
        return None
    
    logger.info(f"Extracted metrics: {metrics}, cities: {cities}, years: {years}, months: {months}")

    # Generate queries
    queries = []
    for city in cities:
        matching_tables = await find_tables_with_city(session, city, tables)
        if not matching_tables:
            logger.warning(f"No table found for city '{city}'.")
            continue
        
        for metric in metrics:
            table_name = None
            for table in matching_tables:
                if table.endswith("_df0") and metric in DF0_METRICS:
                    table_name = table
                    break
                elif table.endswith("_df1") and metric in DF1_METRICS:
                    table_name = table
                    break
            if not table_name:
                logger.warning(f"No table with metric '{metric}' found for city '{city}'.")
                continue
            
            for year in years:
                for month in months:
                    date_pattern = f"{year}-{month}%"
                    query = f"SELECT * FROM {table_name} WHERE UPPER(City) = UPPER('{city}') AND date LIKE '{date_pattern}'"
                    logger.info(f"Generated query: {query} for table: {table_name}")
                    queries.append({
                        "table_name": table_name,
                        "query": query,
                        "metric": metric,
                        "city": city,
                        "year": year,
                        "month": month
                    })

    if not queries:
        logger.error("No valid queries generated.")
        return None
    return queries

def get_climategpt_response(question, data_list):
    """Send data to ClimateGPT API and get a human-like response."""
    if not data_list:
        prompt = f"No data was found for the question: '{question}'. Please provide a response based only on this information."
    else:
        comparison_data = []
        for data, query_info in data_list:
            if not data:
                comparison_data.append(f"No data found for {query_info['city']} in {query_info['month']} {query_info['year']} for '{query_info['metric']}'.")
            else:
                row = data[0]
                metric = query_info['metric']
                value = row.get(metric)
                unit = METRIC_UNITS.get(metric, "unknown unit")
                if value is None:
                    comparison_data.append(f"The data for {query_info['city']} in {query_info['month']} {query_info['year']} does not contain '{metric}'. Available data: {json.dumps(row)}")
                else:
                    comparison_data.append(f"For {query_info['city']} in {query_info['month']} {query_info['year']}, '{metric}' is {value} {unit} based on data: {json.dumps(row)}")
        prompt = f"For '{question}': {'; '.join(comparison_data)}. Please provide a response based only on this information."

    url = "https://erasmus.ai/models/climategpt_8b_latest/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    auth = (os.getenv("API_USER"), os.getenv("API_KEY"))
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
    except requests.RequestException as e:
        logger.error(f"API request failed: {str(e)}")
        return f"Error: Could not process request due to API failure: {str(e)}"

async def process_question(user_input, session, tables):
    """Process user input and return a response with details."""
    query_info = await generate_query(user_input, tables, session)
    if query_info is None:
        response = get_climategpt_response(user_input, [])
        return {
            "response": response,
            "queries": [{"selected_table": "None", "extracted_metric": "None", "extracted_city": "None", "extracted_year": "None", "extracted_month": "None"}]
        }

    # Execute each query
    data_list = []
    for query in query_info:
        result = await session.call_tool(
            "query_table",
            arguments={"table_name": query["table_name"], "query": query["query"]}
        )
        data = []
        if result.content:
            for content in result.content:
                try:
                    data.append(json.loads(content.text))
                except json.JSONDecodeError:
                    pass
        data_list.append((data, query))

    response = get_climategpt_response(user_input, data_list)
    return {
        "response": response,
        "queries": [
            {
                "selected_table": q["table_name"],
                "extracted_metric": q["metric"],
                "extracted_city": q["city"],
                "extracted_year": q["year"],
                "extracted_month": q["month"]
            } for q in query_info
        ]
    }

async def main():
    print("Climate Chatbot - Type 'exit' to quit")
    server_params = StdioServerParameters(
        command="python",
        args=["era5mcp.py"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("list_tables", arguments={})
            tables = [content.text for content in result.content] if result.content else []
            logger.info(f"Available tables: {tables}")

            while True:
                user_input = input("Enter your question: ").strip()
                if user_input.lower() == "exit":
                    print("Goodbye!")
                    break
                try:
                    result = await process_question(user_input, session, tables)
                    for i, query in enumerate(result['queries'], 1):
                        print(f"\nQuery {i}:")
                        print(f"  Selected Table: {query['selected_table']}")
                        print(f"  Extracted Metric: {query['extracted_metric']}")
                        print(f"  Extracted City: {query['extracted_city']}")
                        print(f"  Extracted Year: {query['extracted_year']}")
                        print(f"  Extracted Month: {query['extracted_month']}")
                    print()
                    print("Question:", user_input)
                    print(f"Response: {result['response']}")
                except Exception as e:
                    logger.error(f"Client error: {str(e)}", exc_info=True)
                    print(f"Error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())