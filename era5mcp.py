import sqlite3
from typing import List, Dict
from mcp.server.fastmcp import FastMCP
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("EnvironmentalDataServer", dependencies=["mcp"])

# Path to the SQLite database
DB_PATH = r".\DB's\south_asia_monthly_new.db"

# Tool to list all tables
@mcp.tool()
def list_tables() -> List[str]:
    """List all tables in the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall() if row[0] != 'sqlite_sequence']
        conn.close()
        logger.info(f"Listed tables: {tables}")
        return tables  # Return the list directly as JSON-serializable
    except sqlite3.Error as e:
        logger.error(f"Error listing tables: {str(e)}")
        raise ValueError(f"Error listing tables: {str(e)}")

# Tool to query a specific table
@mcp.tool()
def query_table(table_name: str, query: str) -> List[Dict]:
    """
    Execute a SELECT query on the specified table.
    Args:
        table_name: Name of the table to query (e.g., 'india_df0').
        query: SQL SELECT query (e.g., 'SELECT * FROM table_name WHERE City = "7LC"').
    Returns:
        List of dictionaries where each dictionary represents a row.
    """
    if not table_name in list_tables():
        logger.error(f"Invalid table name: {table_name}")
        raise ValueError(f"Table '{table_name}' does not exist.")
    if not query.strip().upper().startswith("SELECT"):
        logger.error("Non-SELECT query attempted")
        raise ValueError("Only SELECT queries are allowed.")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        safe_query = query.replace("FROM table_name", f"FROM {table_name}")
        cursor.execute(safe_query)
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
        conn.close()
        logger.info(f"Query executed on {table_name}: {safe_query}")
        return results  # Return the list of dicts directly
    except sqlite3.Error as e:
        logger.error(f"Database error: {str(e)}")
        raise ValueError(f"Database error: {str(e)}")

# Tool to get sample data from a table
@mcp.tool()
def get_sample_data(table_name: str) -> List[Dict]:
    """
    Fetch up to 5 rows from the specified table.
    Args:
        table_name: Name of the table (e.g., 'india_df0').
    Returns:
        List of dictionaries with up to 5 rows.
    """
    if not table_name in list_tables():
        logger.error(f"Invalid table name: {table_name}")
        raise ValueError(f"Table '{table_name}' does not exist.")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
        conn.close()
        logger.info(f"Fetched sample data from {table_name}")
        return results  # Return the list of dicts directly
    except sqlite3.Error as e:
        logger.error(f"Error fetching sample data: {str(e)}")
        raise ValueError(f"Error fetching sample data: {str(e)}")

# Run the server
if __name__ == "__main__":
    logger.info("Starting MCP server")
    mcp.run()