import os
from langchain_openai import ChatOpenAI
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

# 1. Define the exact structure you want the AI to return
class PlayerStatus(BaseModel):
    player_name: str = Field(description="Name of the player mentioned")
    status: str = Field(description="E.g., Out, Playing, Questionable, Minutes Restriction")
    impact_score: float = Field(description="Penalty to win_prob from 0.0 (no impact) to 0.5 (severe)")
    is_confirmed: bool = Field(description="True if from an official team source or top reporter")

def create_news_agent():
    """Initializes the LangChain agent for parsing unstructured sports news."""
    # Use a fast model with structured output capabilities
    llm = ChatOpenAI(
        model="gpt-4o-mini", 
        api_key=os.getenv("OPENAI_API_KEY")
    ).with_structured_output(PlayerStatus)

    # Create the prompt template
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert sports betting news parser. Extract injury and lineup data from the following tweet or news snippet. If no injury is mentioned, output a 0.0 impact score."),
        ("user", "{news_text}")
    ])
    
    # Return the LangChain LCEL pipeline
    return prompt | llm

if __name__ == "__main__":
    # Example usage:
    agent = create_news_agent()
    tweet = "Per Coach, Anthony Davis will be on a strict 20-minute restriction tonight due to ankle soreness."
    
    result = agent.invoke({"news_text": tweet})
    print(result.json(indent=2))
    # Output will be a clean, parsed PlayerStatus object
