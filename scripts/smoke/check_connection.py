from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv
import os

load_dotenv()

# Test connection to your Aura database
graph = Neo4jGraph(
    url=os.getenv("NEO4J_URI"),
    username=os.getenv("NEO4J_USERNAME"),
    password=os.getenv("NEO4J_PASSWORD"),
    database=os.getenv("NEO4J_DATABASE")
)

try:
    result = graph.query("RETURN 'Connection successful' as message")
    print("✅ Connection successful!")
    print(f"Result: {result}")
except Exception as e:
    print(f"❌ Connection failed: {e}")