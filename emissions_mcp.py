from mcp.server.fastmcp import FastMCP
import sqlite3

# Create the MCP server instance
mcp = FastMCP("Multi-Database Emission Server")

# Supported databases
DATABASES = {
    "n2o": "./DB's/N2o_emissions.db",
    "fluorinated": "./DB's/Flourinated_gas_emissions.db",
    "co2": "./DB's/co2_emissions.db",
    "ch4": "./DB's/methane_emissions.db"
}

def connect_db(db_key: str):
    """Helper to connect to the correct database"""
    if db_key not in DATABASES:
        raise ValueError(f"Unsupported database: {db_key}")
    return sqlite3.connect(DATABASES[db_key])

# Resource to expose the schema of a specific database
@mcp.resource("schema://{db_key}")
def fetch_schema(db_key: str) -> str:
    """Return schema for selected database key"""
    try:
        conn = connect_db(db_key)
        cursor = conn.cursor()
        schema = cursor.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        return "\n".join(sql[0] for sql in schema if sql[0])
    except Exception as e:
        return f"Error fetching schema: {str(e)}"

# Tool to query the selected SQLite database
@mcp.tool()
def query_data(db_key: str, sql: str) -> str:
    """Execute SQL query on specified database"""
    try:
        conn = connect_db(db_key)
        cursor = conn.cursor()
        result = cursor.execute(sql).fetchall()
        conn.close()

        if not result:
            return "No data found for the query."
        return "\n".join(str(row) for row in result)
    except Exception as e:
        return f"Error executing query: {str(e)}"

# Tool to fetch country names from a specific DB
@mcp.tool()
def get_country_names(db_key: str, substance: str = None) -> list:
    """Return distinct country names from emissions table of a database, optionally filtered by substance"""
    try:
        conn = connect_db(db_key)
        cursor = conn.cursor()
        if substance:
            cursor.execute("SELECT DISTINCT Name FROM emissions WHERE Substance = ?", (substance,))
        else:
            cursor.execute("SELECT DISTINCT Name FROM emissions")
        countries = [row[0] for row in cursor.fetchall()]
        conn.close()
        return countries
    except Exception as e:
        return [f"Error fetching country names: {str(e)}"]

# Run the server
if __name__ == "__main__":
    print("Starting MCP server...")
    mcp.run()