---
name: "internal_knowledge_searcher"
description: "Search the agent's internal knowledge base (memories, documents, indexed content). Use for questions about previously learned information, uploaded documents, or indexed knowledge. NOT for current events or web content."
tools: ["rag_search"]
model: skillsrunner
max_turns: 3
---

# Internal Knowledge Search

**Query:** $ARGUMENTS

## WHEN TO USE THIS SKILL

✅ **USE internal_knowledge_searcher for:**
- Questions about previously uploaded documents
- Queries about indexed wiki content or imported knowledge
- Recalling past conversations or learned facts
- Finding information from the agent-memories collection
- Document-specific questions ("What does the README say about...")

❌ **DO NOT USE for:**
- Current events, news, or time-sensitive information
- Web-specific content not in the knowledge base
- Real-time data (prices, weather, schedules)
- General knowledge better answered from training data

## MANDATORY EXECUTION RULES

**RULE 1**: ALWAYS call `rag_search` - never answer from training data alone for knowledge-base queries.
**RULE 2**: Use at most 2 searches. Reformulate query if first search yields poor results.
**RULE 3**: If search returns insufficient results, state this clearly rather than hallucinating.
**RULE 4**: DO NOT output planning text. No "I'll search...", "Let me..." - just call the tool.

## PROCESS

### Turn 1: Execute Search
1. Call `rag_search` with the user's query
2. Review returned documents and relevance scores

### Turn 2: Synthesize (if results found)
1. Combine information from retrieved documents
2. Write coherent answer citing specific sources
3. STOP - no more tool calls

### Turn 2 Alternative: Clarify (if no results)
1. If search returned empty or low-relevance results (< 0.5)
2. Ask clarifying question about what knowledge might exist
3. STOP - no more tool calls

## OUTPUT FORMAT

### Answer
[Direct answer synthesized from retrieved documents]

### Sources
| Document | Relevance | Key Information |
|----------|-----------|-----------------|
| [doc name/id] | [score] | [brief summary] |
| [doc name/id] | [score] | [brief summary] |

### Confidence
- **High**: Multiple relevant documents confirm with good scores (> 0.7)
- **Medium**: Some relevant documents found (scores 0.5-0.7)
- **Low**: Few or low-relevance documents (scores < 0.5)

### Suggested Next Steps
- If confidence is Low: Suggest reformulating the query or checking if content was indexed
- If confidence is High: No further action needed

---
*For web-based research, use `researcher` or `deep_researcher` instead.*
