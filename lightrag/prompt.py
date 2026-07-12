from __future__ import annotations
from typing import Any


PROMPTS: dict[str, Any] = {}

# All delimiters must be formatted as "<|UPPER_CASE_STRING|>"
PROMPTS["DEFAULT_TUPLE_DELIMITER"] = "<|#|>"
PROMPTS["DEFAULT_COMPLETION_DELIMITER"] = "<|COMPLETE|>"

PROMPTS["entity_extraction_system_prompt"] = """---Role---
You are a Knowledge Graph Specialist responsible for extracting entities and relationships from the input text.

---Instructions---
1.  **Entity Extraction & Output:**
    *   **Identification:** Identify clearly defined and meaningful entities in the input text.
    *   **Entity Details:** For each identified entity, extract the following information:
        *   `entity_name`: The name of the entity. If the entity name is case-insensitive, capitalize the first letter of each significant word (title case). Ensure **consistent naming** across the entire extraction process.
        *   `entity_type`: Categorize the entity using one of the following types: `{entity_types}`. If none of the provided entity types apply, do not add new entity type and classify it as `Other`.
        *   `entity_description`: Provide a concise yet comprehensive description of the entity's attributes and activities, based *solely* on the information present in the input text.
    *   **Output Format - Entities:** Output a total of 4 fields for each entity, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `entity`.
        *   Format: `entity{tuple_delimiter}entity_name{tuple_delimiter}entity_type{tuple_delimiter}entity_description`

2.  **Relationship Extraction & Output:**
    *   **Identification:** Identify direct, clearly stated, and meaningful relationships between previously extracted entities.
    *   **N-ary Relationship Decomposition:** If a single statement describes a relationship involving more than two entities (an N-ary relationship), decompose it into multiple binary (two-entity) relationship pairs for separate description.
        *   **Example:** For "Alice, Bob, and Carol collaborated on Project X," extract binary relationships such as "Alice collaborated with Project X," "Bob collaborated with Project X," and "Carol collaborated with Project X," or "Alice collaborated with Bob," based on the most reasonable binary interpretations.
    *   **Relationship Details:** For each binary relationship, extract the following fields:
        *   `source_entity`: The name of the source entity. Ensure **consistent naming** with entity extraction. Capitalize the first letter of each significant word (title case) if the name is case-insensitive.
        *   `target_entity`: The name of the target entity. Ensure **consistent naming** with entity extraction. Capitalize the first letter of each significant word (title case) if the name is case-insensitive.
        *   `relationship_keywords`: One or more high-level keywords summarizing the overarching nature, concepts, or themes of the relationship. Multiple keywords within this field must be separated by a comma `,`. **DO NOT use `{tuple_delimiter}` for separating multiple keywords within this field.**
        *   `relationship_description`: A concise explanation of the nature of the relationship between the source and target entities, providing a clear rationale for their connection.
    *   **Output Format - Relationships:** Output a total of 5 fields for each relationship, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `relation`.
        *   Format: `relation{tuple_delimiter}source_entity{tuple_delimiter}target_entity{tuple_delimiter}relationship_keywords{tuple_delimiter}relationship_description`

3.  **Delimiter Usage Protocol:**
    *   The `{tuple_delimiter}` is a complete, atomic marker and **must not be filled with content**. It serves strictly as a field separator.
    *   **Incorrect Example:** `entity{tuple_delimiter}Tokyo<|location|>Tokyo is the capital of Japan.`
    *   **Correct Example:** `entity{tuple_delimiter}Tokyo{tuple_delimiter}location{tuple_delimiter}Tokyo is the capital of Japan.`

4.  **Relationship Direction & Duplication:**
    *   Treat all relationships as **undirected** unless explicitly stated otherwise. Swapping the source and target entities for an undirected relationship does not constitute a new relationship.
    *   Avoid outputting duplicate relationships.

5.  **Output Order & Prioritization:**
    *   Output all extracted entities first, followed by all extracted relationships.
    *   Within the list of relationships, prioritize and output those relationships that are **most significant** to the core meaning of the input text first.

6.  **Context & Objectivity:**
    *   Ensure all entity names and descriptions are written in the **third person**.
    *   Explicitly name the subject or object; **avoid using pronouns** such as `this article`, `this paper`, `our company`, `I`, `you`, and `he/she`.

7.  **Language & Proper Nouns:**
    *   The entire output (entity names, keywords, and descriptions) must be written in `{language}`.
    *   Proper nouns (e.g., personal names, place names, organization names) should be retained in their original language if a proper, widely accepted translation is not available or would cause ambiguity.

8.  **Completion Signal:** Output the literal string `{completion_delimiter}` only after all entities and relationships, following all criteria, have been completely extracted and outputted.

---Examples---
{examples}
"""

PROMPTS["entity_extraction_user_prompt"] = """---Task---
Extract entities and relationships from the input text in Data to be Processed below.

---Instructions---
1.  **Strict Adherence to Format:** Strictly adhere to all format requirements for entity and relationship lists, including output order, field delimiters, and proper noun handling, as specified in the system prompt.
2.  **Output Content Only:** Output *only* the extracted list of entities and relationships. Do not include any introductory or concluding remarks, explanations, or additional text before or after the list.
3.  **Completion Signal:** Output `{completion_delimiter}` as the final line after all relevant entities and relationships have been extracted and presented.
4.  **Output Language:** Ensure the output language is {language}. Proper nouns (e.g., personal names, place names, organization names) must be kept in their original language and not translated.

---Data to be Processed---
<Entity_types>
[{entity_types}]

<Input Text>
```
{input_text}
```

<Output>
"""

PROMPTS["entity_continue_extraction_user_prompt"] = """---Task---
Based on the last extraction task, identify and extract any **missed or incorrectly formatted** entities and relationships from the input text.

---Instructions---
1.  **Strict Adherence to System Format:** Strictly adhere to all format requirements for entity and relationship lists, including output order, field delimiters, and proper noun handling, as specified in the system instructions.
2.  **Focus on Corrections/Additions:**
    *   **Do NOT** re-output entities and relationships that were **correctly and fully** extracted in the last task.
    *   If an entity or relationship was **missed** in the last task, extract and output it now according to the system format.
    *   If an entity or relationship was **truncated, had missing fields, or was otherwise incorrectly formatted** in the last task, re-output the *corrected and complete* version in the specified format.
3.  **Output Format - Entities:** Output a total of 4 fields for each entity, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `entity`.
4.  **Output Format - Relationships:** Output a total of 5 fields for each relationship, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `relation`.
5.  **Output Content Only:** Output *only* the extracted list of entities and relationships. Do not include any introductory or concluding remarks, explanations, or additional text before or after the list.
6.  **Completion Signal:** Output `{completion_delimiter}` as the final line after all relevant missing or corrected entities and relationships have been extracted and presented.
7.  **Output Language:** Ensure the output language is {language}. Proper nouns (e.g., personal names, place names, organization names) must be kept in their original language and not translated.

<Output>
"""

PROMPTS["entity_extraction_examples"] = [
    """<Entity_types>
["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject"]

<Input Text>
```
while Alex clenched his jaw, the buzz of frustration dull against the backdrop of Taylor's authoritarian certainty. It was this competitive undercurrent that kept him alert, the sense that his and Jordan's shared commitment to discovery was an unspoken rebellion against Cruz's narrowing vision of control and order.

Then Taylor did something unexpected. They paused beside Jordan and, for a moment, observed the device with something akin to reverence. "If this tech can be understood..." Taylor said, their voice quieter, "It could change the game for us. For all of us."

The underlying dismissal earlier seemed to falter, replaced by a glimpse of reluctant respect for the gravity of what lay in their hands. Jordan looked up, and for a fleeting heartbeat, their eyes locked with Taylor's, a wordless clash of wills softening into an uneasy truce.

It was a small transformation, barely perceptible, but one that Alex noted with an inward nod. They had all been brought here by different paths
```

<Output>
entity{tuple_delimiter}Alex{tuple_delimiter}person{tuple_delimiter}Alex is a character who experiences frustration and is observant of the dynamics among other characters.
entity{tuple_delimiter}Taylor{tuple_delimiter}person{tuple_delimiter}Taylor is portrayed with authoritarian certainty and shows a moment of reverence towards a device, indicating a change in perspective.
entity{tuple_delimiter}Jordan{tuple_delimiter}person{tuple_delimiter}Jordan shares a commitment to discovery and has a significant interaction with Taylor regarding a device.
entity{tuple_delimiter}Cruz{tuple_delimiter}person{tuple_delimiter}Cruz is associated with a vision of control and order, influencing the dynamics among other characters.
entity{tuple_delimiter}The Device{tuple_delimiter}equipment{tuple_delimiter}The Device is central to the story, with potential game-changing implications, and is revered by Taylor.
relation{tuple_delimiter}Alex{tuple_delimiter}Taylor{tuple_delimiter}power dynamics, observation{tuple_delimiter}Alex observes Taylor's authoritarian behavior and notes changes in Taylor's attitude toward the device.
relation{tuple_delimiter}Alex{tuple_delimiter}Jordan{tuple_delimiter}shared goals, rebellion{tuple_delimiter}Alex and Jordan share a commitment to discovery, which contrasts with Cruz's vision.)
relation{tuple_delimiter}Taylor{tuple_delimiter}Jordan{tuple_delimiter}conflict resolution, mutual respect{tuple_delimiter}Taylor and Jordan interact directly regarding the device, leading to a moment of mutual respect and an uneasy truce.
relation{tuple_delimiter}Jordan{tuple_delimiter}Cruz{tuple_delimiter}ideological conflict, rebellion{tuple_delimiter}Jordan's commitment to discovery is in rebellion against Cruz's vision of control and order.
relation{tuple_delimiter}Taylor{tuple_delimiter}The Device{tuple_delimiter}reverence, technological significance{tuple_delimiter}Taylor shows reverence towards the device, indicating its importance and potential impact.
{completion_delimiter}

""",
    """<Entity_types>
["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject"]

<Input Text>
```
Stock markets faced a sharp downturn today as tech giants saw significant declines, with the global tech index dropping by 3.4% in midday trading. Analysts attribute the selloff to investor concerns over rising interest rates and regulatory uncertainty.

Among the hardest hit, nexon technologies saw its stock plummet by 7.8% after reporting lower-than-expected quarterly earnings. In contrast, Omega Energy posted a modest 2.1% gain, driven by rising oil prices.

Meanwhile, commodity markets reflected a mixed sentiment. Gold futures rose by 1.5%, reaching $2,080 per ounce, as investors sought safe-haven assets. Crude oil prices continued their rally, climbing to $87.60 per barrel, supported by supply constraints and strong demand.

Financial experts are closely watching the Federal Reserve's next move, as speculation grows over potential rate hikes. The upcoming policy announcement is expected to influence investor confidence and overall market stability.
```

<Output>
entity{tuple_delimiter}Global Tech Index{tuple_delimiter}category{tuple_delimiter}The Global Tech Index tracks the performance of major technology stocks and experienced a 3.4% decline today.
entity{tuple_delimiter}Nexon Technologies{tuple_delimiter}organization{tuple_delimiter}Nexon Technologies is a tech company that saw its stock decline by 7.8% after disappointing earnings.
entity{tuple_delimiter}Omega Energy{tuple_delimiter}organization{tuple_delimiter}Omega Energy is an energy company that gained 2.1% in stock value due to rising oil prices.
entity{tuple_delimiter}Gold Futures{tuple_delimiter}product{tuple_delimiter}Gold futures rose by 1.5%, indicating increased investor interest in safe-haven assets.
entity{tuple_delimiter}Crude Oil{tuple_delimiter}product{tuple_delimiter}Crude oil prices rose to $87.60 per barrel due to supply constraints and strong demand.
entity{tuple_delimiter}Market Selloff{tuple_delimiter}category{tuple_delimiter}Market selloff refers to the significant decline in stock values due to investor concerns over interest rates and regulations.
entity{tuple_delimiter}Federal Reserve Policy Announcement{tuple_delimiter}category{tuple_delimiter}The Federal Reserve's upcoming policy announcement is expected to impact investor confidence and market stability.
entity{tuple_delimiter}3.4% Decline{tuple_delimiter}category{tuple_delimiter}The Global Tech Index experienced a 3.4% decline in midday trading.
relation{tuple_delimiter}Global Tech Index{tuple_delimiter}Market Selloff{tuple_delimiter}market performance, investor sentiment{tuple_delimiter}The decline in the Global Tech Index is part of the broader market selloff driven by investor concerns.
relation{tuple_delimiter}Nexon Technologies{tuple_delimiter}Global Tech Index{tuple_delimiter}company impact, index movement{tuple_delimiter}Nexon Technologies' stock decline contributed to the overall drop in the Global Tech Index.
relation{tuple_delimiter}Gold Futures{tuple_delimiter}Market Selloff{tuple_delimiter}market reaction, safe-haven investment{tuple_delimiter}Gold prices rose as investors sought safe-haven assets during the market selloff.
relation{tuple_delimiter}Federal Reserve Policy Announcement{tuple_delimiter}Market Selloff{tuple_delimiter}interest rate impact, financial regulation{tuple_delimiter}Speculation over Federal Reserve policy changes contributed to market volatility and investor selloff.
{completion_delimiter}

""",
    """<Entity_types>
["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject"]

<Input Text>
```
At the World Athletics Championship in Tokyo, Noah Carter broke the 100m sprint record using cutting-edge carbon-fiber spikes.
```

<Output>
entity{tuple_delimiter}World Athletics Championship{tuple_delimiter}event{tuple_delimiter}The World Athletics Championship is a global sports competition featuring top athletes in track and field.
entity{tuple_delimiter}Tokyo{tuple_delimiter}location{tuple_delimiter}Tokyo is the host city of the World Athletics Championship.
entity{tuple_delimiter}Noah Carter{tuple_delimiter}person{tuple_delimiter}Noah Carter is a sprinter who set a new record in the 100m sprint at the World Athletics Championship.
entity{tuple_delimiter}100m Sprint Record{tuple_delimiter}category{tuple_delimiter}The 100m sprint record is a benchmark in athletics, recently broken by Noah Carter.
entity{tuple_delimiter}Carbon-Fiber Spikes{tuple_delimiter}equipment{tuple_delimiter}Carbon-fiber spikes are advanced sprinting shoes that provide enhanced speed and traction.
entity{tuple_delimiter}World Athletics Federation{tuple_delimiter}organization{tuple_delimiter}The World Athletics Federation is the governing body overseeing the World Athletics Championship and record validations.
relation{tuple_delimiter}World Athletics Championship{tuple_delimiter}Tokyo{tuple_delimiter}event location, international competition{tuple_delimiter}The World Athletics Championship is being hosted in Tokyo.
relation{tuple_delimiter}Noah Carter{tuple_delimiter}100m Sprint Record{tuple_delimiter}athlete achievement, record-breaking{tuple_delimiter}Noah Carter set a new 100m sprint record at the championship.
relation{tuple_delimiter}Noah Carter{tuple_delimiter}Carbon-Fiber Spikes{tuple_delimiter}athletic equipment, performance boost{tuple_delimiter}Noah Carter used carbon-fiber spikes to enhance performance during the race.
relation{tuple_delimiter}Noah Carter{tuple_delimiter}World Athletics Championship{tuple_delimiter}athlete participation, competition{tuple_delimiter}Noah Carter is competing at the World Athletics Championship.
{completion_delimiter}

""",
]

PROMPTS["summarize_entity_descriptions"] = """---Role---
You are a Knowledge Graph Specialist, proficient in data curation and synthesis.

---Task---
Your task is to synthesize a list of descriptions of a given entity or relation into a single, comprehensive, and cohesive summary.

---Instructions---
1. Input Format: The description list is provided in JSON format. Each JSON object (representing a single description) appears on a new line within the `Description List` section.
2. Output Format: The merged description will be returned as plain text, presented in multiple paragraphs, without any additional formatting or extraneous comments before or after the summary.
3. Comprehensiveness: The summary must integrate all key information from *every* provided description. Do not omit any important facts or details.
4. Context: Ensure the summary is written from an objective, third-person perspective; explicitly mention the name of the entity or relation for full clarity and context.
5. Context & Objectivity:
  - Write the summary from an objective, third-person perspective.
  - Explicitly mention the full name of the entity or relation at the beginning of the summary to ensure immediate clarity and context.
6. Conflict Handling:
  - In cases of conflicting or inconsistent descriptions, first determine if these conflicts arise from multiple, distinct entities or relationships that share the same name.
  - If distinct entities/relations are identified, summarize each one *separately* within the overall output.
  - If conflicts within a single entity/relation (e.g., historical discrepancies) exist, attempt to reconcile them or present both viewpoints with noted uncertainty.
7. Length Constraint:The summary's total length must not exceed {summary_length} tokens, while still maintaining depth and completeness.
8. Language: The entire output must be written in {language}. Proper nouns (e.g., personal names, place names, organization names) may in their original language if proper translation is not available.
  - The entire output must be written in {language}.
  - Proper nouns (e.g., personal names, place names, organization names) should be retained in their original language if a proper, widely accepted translation is not available or would cause ambiguity.

---Input---
{description_type} Name: {description_name}

Description List:

```
{description_list}
```

---Output---
"""

PROMPTS["fail_response"] = (
    "Sorry, I'm not able to provide an answer to that question.[no-context]"
)

PROMPTS["rag_response"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Knowledge Graph and Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - Scrutinize both `Knowledge Graph Data` and `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a references section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{context_data}
"""

PROMPTS["rag_response_annotated"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Annotated Source Documents found in the **Context**.
Each source document includes the original text followed by structured knowledge (entities, relationships, and decision context) extracted from it. Use both the prose text and the structured annotations to build your response.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - For each Annotated Source Document, read both the source text AND the Extracted Knowledge section. The annotations highlight key entities, their relationships, and any decision context (temporal validity, approval chains, quantitative data) that enriches the source text.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a references section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.
  - When decision context is available (approval chains, temporal validity, confidence scores), incorporate these details to provide authoritative answers.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{context_data}
"""

PROMPTS["naive_rag_response"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - Scrutinize `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a **References** section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{content_data}
"""

PROMPTS["kg_query_context"] = """
Knowledge Graph Data (Entity):

```json
{entities_str}
```

Knowledge Graph Data (Relationship):

```json
{relations_str}
```

Document Chunks (Each entry has a reference_id refer to the `Reference Document List`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```

"""

PROMPTS["kg_annotated_context"] = """
Annotated Source Documents (each source chunk is followed by structured knowledge extracted from it):

{annotated_chunks_str}

{additional_graph_facts_str}
Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Annotated Source Documents):

```
{reference_list_str}
```

"""

PROMPTS["naive_query_context"] = """
Document Chunks (Each entry has a reference_id refer to the `Reference Document List`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```

"""

PROMPTS["keywords_extraction"] = """---Role---
You are an expert keyword extractor, specializing in analyzing user queries for a Retrieval-Augmented Generation (RAG) system. Your purpose is to identify both high-level and low-level keywords in the user's query that will be used for effective document retrieval.

---Goal---
Given a user query, your task is to extract two distinct types of keywords:
1. **high_level_keywords**: for overarching concepts or themes, capturing user's core intent, the subject area, or the type of question being asked.
2. **low_level_keywords**: for specific entities or details, identifying the specific entities, proper nouns, technical jargon, product names, or concrete items.

---Instructions & Constraints---
1. **Output Format**: Your output MUST be a valid JSON object and nothing else. Do not include any explanatory text, markdown code fences (like ```json), or any other text before or after the JSON. It will be parsed directly by a JSON parser.
2. **Source of Truth**: All keywords must be explicitly derived from the user query, with both high-level and low-level keyword categories are required to contain content.
3. **Concise & Meaningful**: Keywords should be concise words or meaningful phrases. Prioritize multi-word phrases when they represent a single concept. For example, from "latest financial report of Apple Inc.", you should extract "latest financial report" and "Apple Inc." rather than "latest", "financial", "report", and "Apple".
4. **Handle Edge Cases**: For queries that are too simple, vague, or nonsensical (e.g., "hello", "ok", "asdfghjkl"), you must return a JSON object with empty lists for both keyword types.
5. **Language**: All extracted keywords MUST be in {language}. Proper nouns (e.g., personal names, place names, organization names) should be kept in their original language.

---Examples---
{examples}

---Real Data---
User Query: {query}

---Output---
Output:"""

PROMPTS["keywords_extraction_examples"] = [
    """Example 1:

Query: "How does international trade influence global economic stability?"

Output:
{
  "high_level_keywords": ["International trade", "Global economic stability", "Economic impact"],
  "low_level_keywords": ["Trade agreements", "Tariffs", "Currency exchange", "Imports", "Exports"]
}

""",
    """Example 2:

Query: "What are the environmental consequences of deforestation on biodiversity?"

Output:
{
  "high_level_keywords": ["Environmental consequences", "Deforestation", "Biodiversity loss"],
  "low_level_keywords": ["Species extinction", "Habitat destruction", "Carbon emissions", "Rainforest", "Ecosystem"]
}

""",
    """Example 3:

Query: "What is the role of education in reducing poverty?"

Output:
{
  "high_level_keywords": ["Education", "Poverty reduction", "Socioeconomic development"],
  "low_level_keywords": ["School access", "Literacy rates", "Job training", "Income inequality"]
}

""",
]

# ─────────────────────────────────────────────────────────────────────────────
# Context Graph (CG) prompts — extend LightRAG with contextual quadruples
# (h, r, t, rc) where rc is the Relation Context capturing the "why".
# ─────────────────────────────────────────────────────────────────────────────

PROMPTS["cg_entity_extraction_system_prompt"] = """---Role---
You are a Context Graph Specialist responsible for extracting entities and contextual relationships from the input text. Your goal is to capture not just *what* relationships exist, but *why* they exist — the decisions, evidence, temporal validity, and source provenance behind each link.

---Instructions---
1.  **Entity Extraction & Output:**
    *   Identify clearly defined and meaningful entities in the input text. Extract only **specific, named, referenceable** entities — a person, organization, system, product, technology, policy, event, or a concept that has a proper name.
    *   **Do NOT extract** (these pollute the graph): pronouns or deictic references (`it`, `this`, `they`, `the system`, `our approach`); bare generic nouns with no specific referent (`the process`, `performance`, `best practices`); or opaque identifiers that are not meaningful entities on their own (raw commit hashes, log lines, environment-variable names, file paths) — unless the text gives them a clear, specific meaning.
    *   Prefer the entity's **canonical, fullest name** and use it consistently throughout.
    *   For each entity, extract:
        *   `entity_name`: Name of the entity (title-case if case-insensitive; consistent naming throughout).
        *   `entity_type`: One of `{entity_types}`, or `Other` if none apply.
        *   `entity_description`: Concise, objective, third-person description based *solely* on the text.
    *   **Output Format — Entities:** 4 fields delimited by `{tuple_delimiter}`, first field must be literal `entity`:
        *   `entity{tuple_delimiter}entity_name{tuple_delimiter}entity_type{tuple_delimiter}entity_description`

2.  **Relationship Extraction with Relation Context (rc):**
    *   Identify direct, clearly stated relationships between extracted entities.
    *   **Connect what you extract:** every entity you output should participate in at least one relationship where the text supports it — prefer relating an entity to another over leaving it unconnected. Do NOT invent relationships the text doesn't state, but do capture the ones it does (including implicit-but-clear links like membership, authorship, location, causation, or part-of).
    *   For each relationship, extract the standard fields PLUS a compact JSON **Relation Context** as the 6th field.
    *   **Standard fields (1–5):**
        *   `source_entity`: Name of source entity (consistent with entity extraction).
        *   `target_entity`: Name of target entity (consistent with entity extraction).
        *   `relationship_keywords`: Comma-separated high-level keywords (no `{tuple_delimiter}` inside this field).
        *   `relationship_description`: Concise explanation of the relationship.
    *   **6th field — Relation Context JSON:**
        *   A **single-line, compact JSON object** (no newlines or pretty-printing) with these keys:
            *   `"supporting_sentences"`: Array of up to 3 direct verbatim quotes from the text that support this relationship. Use `[]` if none.
            *   `"temporal_info"`: Validity period or timestamp (e.g., `"Q4 2026"`, `"since 2020"`), or `null`.
            *   `"quantitative_data"`: Numerical data (amounts, percentages, counts), or `null`.
            *   `"decision_trace"`: The rationale, exception, or approval behind this relationship (the "why"), or `null`.
            *   `"approved_by"`: Name of the person or team who approved this decision (e.g., `"VP_Smith"`, `"Finance_Team"`), or `null` if not mentioned.
            *   `"approved_via"`: Channel through which approval was given — one of `"slack"`, `"zoom"`, `"email"`, `"in_person"`, `"jira"`, `"system"` — or `null` if not mentioned.
            *   `"valid_from"`: ISO-8601 date (`"YYYY-MM-DD"`) when this decision became effective, or `null` if not stated.
            *   `"valid_until"`: ISO-8601 date (`"YYYY-MM-DD"`) when this decision expires, or `null` if not stated.
            *   `"policy_ref"`: Name or ID of the policy this decision follows or overrides (e.g., `"DiscountPolicy_Standard"`), or `null` if not mentioned.
            *   `"provenance"`: Source reference (speaker name, document section, timestamp), or `null`.
            *   `"confidence_score"`: Float 0.0–1.0 indicating extraction confidence based on text clarity.
        *   Fill `approved_by`/`approved_via` when the text mentions who approved something and how. Fill `valid_from`/`valid_until` when explicit dates are given. Fill `policy_ref` when a policy name or ID is referenced.
        *   The JSON must NOT contain `{tuple_delimiter}` characters.
    *   **Output Format — Relationships:** 6 fields delimited by `{tuple_delimiter}`, first field must be literal `relation`:
        *   `relation{tuple_delimiter}source_entity{tuple_delimiter}target_entity{tuple_delimiter}relationship_keywords{tuple_delimiter}relationship_description{tuple_delimiter}RELATION_CONTEXT_JSON`

3.  **Delimiter Usage Protocol:**
    *   `{tuple_delimiter}` is an atomic field separator — never use it *inside* a field value.
    *   The RELATION_CONTEXT_JSON field must be a single JSON object on the same line.

4.  **Relationship Direction & Duplication:**
    *   Treat all relationships as undirected unless stated otherwise.
    *   Avoid duplicate relationships.

5.  **Output Order:** All entities first, then all relationships (most significant first).

6.  **Context & Objectivity:** Third-person perspective; no pronouns; explicit entity names.

7.  **Language & Proper Nouns:** Output in `{language}`; keep proper nouns in original language.

8.  **Completion Signal:** Output `{completion_delimiter}` after all extraction is complete.

---Examples---
{examples}
"""

PROMPTS["cg_entity_extraction_examples"] = [
    """<Entity_types>
["Person","Organization","Location","Event","Concept","Artifact"]

<Input Text>
```
During the Q3 2024 business review, Sarah Chen (VP of Sales) approved a 20% discount for MegaCorp's enterprise deal, citing their five-year relationship and a competing offer from Salesforce. The discount was valid until December 31, 2024. This was discussed in the Slack channel #deals-review on August 14, 2024.
```

<Output>
entity{tuple_delimiter}Sarah Chen{tuple_delimiter}person{tuple_delimiter}Sarah Chen is the VP of Sales who approved a 20% discount for MegaCorp's enterprise deal during the Q3 2024 business review.
entity{tuple_delimiter}MegaCorp{tuple_delimiter}organization{tuple_delimiter}MegaCorp is an enterprise client that received a 20% discount on a deal, citing a long-standing relationship and competitive pressure from Salesforce.
entity{tuple_delimiter}Salesforce{tuple_delimiter}organization{tuple_delimiter}Salesforce is a competitor that made a competing offer to MegaCorp, influencing the discount approval.
entity{tuple_delimiter}Q3 2024 Business Review{tuple_delimiter}event{tuple_delimiter}The Q3 2024 Business Review is an internal meeting during which the discount for MegaCorp was approved by Sarah Chen.
relation{tuple_delimiter}Sarah Chen{tuple_delimiter}MegaCorp{tuple_delimiter}discount approval, deal negotiation{tuple_delimiter}Sarah Chen approved a 20% discount for MegaCorp's enterprise deal during the Q3 2024 business review.{tuple_delimiter}{{"supporting_sentences": ["Sarah Chen (VP of Sales) approved a 20% discount for MegaCorp's enterprise deal"], "temporal_info": "Valid until December 31, 2024", "quantitative_data": "20% discount", "decision_trace": "Approved citing five-year relationship and competing offer from Salesforce", "approved_by": "Sarah Chen", "approved_via": "in_person", "valid_from": null, "valid_until": "2024-12-31", "policy_ref": null, "provenance": "Slack #deals-review, August 14, 2024", "confidence_score": 0.97}}
relation{tuple_delimiter}MegaCorp{tuple_delimiter}Salesforce{tuple_delimiter}competitive pressure, market competition{tuple_delimiter}Salesforce made a competing offer to MegaCorp that influenced the discount negotiation.{tuple_delimiter}{{"supporting_sentences": ["a competing offer from Salesforce"], "temporal_info": "Q3 2024", "quantitative_data": null, "decision_trace": "Competing offer used as justification for discount approval", "approved_by": null, "approved_via": null, "valid_from": null, "valid_until": null, "policy_ref": null, "provenance": "Q3 2024 Business Review", "confidence_score": 0.88}}
{completion_delimiter}

""",
    """<Entity_types>
["Person","Organization","Location","Event","Concept","Artifact"]

<Input Text>
```
Barack Obama served as the 44th President of the United States from January 20, 2009 to January 20, 2017. His administration passed the Affordable Care Act in 2010, expanding health insurance coverage to millions of Americans.
```

<Output>
entity{tuple_delimiter}Barack Obama{tuple_delimiter}person{tuple_delimiter}Barack Obama is the 44th President of the United States who served from 2009 to 2017 and oversaw the passage of the Affordable Care Act.
entity{tuple_delimiter}United States{tuple_delimiter}location{tuple_delimiter}The United States is the country led by Barack Obama during his presidency from 2009 to 2017.
entity{tuple_delimiter}Affordable Care Act{tuple_delimiter}concept{tuple_delimiter}The Affordable Care Act is landmark healthcare legislation passed in 2010 that expanded health insurance coverage to millions of Americans.
relation{tuple_delimiter}Barack Obama{tuple_delimiter}United States{tuple_delimiter}political leadership, presidency{tuple_delimiter}Barack Obama served as the 44th President of the United States.{tuple_delimiter}{{"supporting_sentences": ["Barack Obama served as the 44th President of the United States from January 20, 2009 to January 20, 2017"], "temporal_info": "January 20, 2009 – January 20, 2017", "quantitative_data": "44th President", "decision_trace": null, "provenance": null, "confidence_score": 0.99}}
relation{tuple_delimiter}Barack Obama{tuple_delimiter}Affordable Care Act{tuple_delimiter}legislation, policy achievement{tuple_delimiter}Barack Obama's administration passed the Affordable Care Act in 2010, expanding healthcare coverage.{tuple_delimiter}{{"supporting_sentences": ["His administration passed the Affordable Care Act in 2010, expanding health insurance coverage to millions of Americans"], "temporal_info": "2010", "quantitative_data": "millions of Americans covered", "decision_trace": "Policy goal to expand healthcare access", "provenance": null, "confidence_score": 0.98}}
{completion_delimiter}

""",
    """<Entity_types>
["Person","Organization","LossReason","Objection","Competitor","Concept","Artifact"]

<Input Text>
```
Sales Conversation Pattern: Lost Deal
Company: TechGadgets
Conversation Type: Purchasing Assistance
Outcome: Lost (ClosedLost)
Period: January 2026
Value Range: 1,000-2,000
Products Discussed: Premium Wireless Speaker
Conversation Length: 14 messages

## Conversation Transcript

**Customer:** Hi, I'm interested in the Premium Wireless Speaker. What's the best price you can offer?

**Sales Agent:** The Premium Wireless Speaker is listed at $1,499. It's our top-rated product with excellent reviews.

**Customer:** That's quite expensive. I saw the SoundMax Pro from AudioRival for $999 with similar features.

**Sales Agent:** Our product has superior build quality and longer warranty coverage.

**Customer:** I understand, but the price difference is significant. Can you match their price or offer a discount?

**Sales Agent:** Unfortunately, we can't match competitor pricing. Our standard policy doesn't allow discounts beyond 5%.

**Customer:** That's not enough. I'll go with AudioRival then. Thanks anyway.
```

<Output>
entity{tuple_delimiter}Premium Wireless Speaker{tuple_delimiter}artifact{tuple_delimiter}Premium Wireless Speaker is a top-rated product by TechGadgets listed at $1,499, discussed in a lost sales conversation.
entity{tuple_delimiter}AudioRival{tuple_delimiter}competitor{tuple_delimiter}AudioRival is a competitor offering the SoundMax Pro at $999, cited by the customer as an alternative to the Premium Wireless Speaker.
entity{tuple_delimiter}SoundMax Pro{tuple_delimiter}artifact{tuple_delimiter}SoundMax Pro is a competing product from AudioRival priced at $999 with features similar to the Premium Wireless Speaker.
entity{tuple_delimiter}Price Too High{tuple_delimiter}lossreason{tuple_delimiter}The customer found the $1,499 price too high compared to the competitor's $999 offering, a $500 price gap that could not be bridged by the 5% maximum discount policy.
entity{tuple_delimiter}Competitor Pricing Objection{tuple_delimiter}objection{tuple_delimiter}The customer objected to the price by citing a competitor (AudioRival SoundMax Pro at $999) offering similar features at a lower price. The objection was not resolved — the agent could not match or sufficiently close the price gap.
relation{tuple_delimiter}Premium Wireless Speaker{tuple_delimiter}SoundMax Pro{tuple_delimiter}competitive pressure, price comparison{tuple_delimiter}The customer compared the Premium Wireless Speaker ($1,499) against the SoundMax Pro ($999), finding the competitor's price more attractive.{tuple_delimiter}{{"supporting_sentences": ["I saw the SoundMax Pro from AudioRival for $999 with similar features", "the price difference is significant"], "temporal_info": "January 2026", "quantitative_data": "$1,499 vs $999 — $500 price gap", "decision_trace": "Customer chose competitor due to unresolvable price difference. Agent limited to 5% discount by policy.", "approved_by": null, "approved_via": null, "valid_from": null, "valid_until": null, "policy_ref": "Standard 5% maximum discount policy", "provenance": "Lost deal conversation, January 2026", "confidence_score": 0.95}}
relation{tuple_delimiter}Price Too High{tuple_delimiter}Premium Wireless Speaker{tuple_delimiter}deal loss, pricing failure{tuple_delimiter}The high price of the Premium Wireless Speaker ($1,499) was the primary reason for losing this deal, as the agent could not offer a competitive discount.{tuple_delimiter}{{"supporting_sentences": ["That's quite expensive", "Can you match their price or offer a discount?", "That's not enough. I'll go with AudioRival then."], "temporal_info": "January 2026", "quantitative_data": "$1,499 price, 5% max discount ($74.95 off), competitor at $999", "decision_trace": "Deal lost because discount policy capped at 5% could not bridge $500 price gap to competitor", "approved_by": null, "approved_via": null, "valid_from": null, "valid_until": null, "policy_ref": "Standard 5% maximum discount policy", "provenance": "Lost deal conversation", "confidence_score": 0.96}}
relation{tuple_delimiter}Competitor Pricing Objection{tuple_delimiter}AudioRival{tuple_delimiter}objection trigger, competitor reference{tuple_delimiter}The customer's pricing objection was triggered by AudioRival's lower-priced SoundMax Pro offering. The objection was not resolved.{tuple_delimiter}{{"supporting_sentences": ["I saw the SoundMax Pro from AudioRival for $999 with similar features"], "temporal_info": "January 2026", "quantitative_data": "$999 competitor price", "decision_trace": "Unresolved objection — agent could not match competitor pricing, leading to deal loss", "approved_by": null, "approved_via": null, "valid_from": null, "valid_until": null, "policy_ref": null, "provenance": "Lost deal conversation", "confidence_score": 0.94}}
{completion_delimiter}

""",
]

PROMPTS["cg_entity_continue_extraction_user_prompt"] = """---Task---
Based on the last extraction task, identify and extract any **missed or incorrectly formatted** entities and relationships from the input text.

---Instructions---
1.  **Strict Adherence to System Format:** Follow all format requirements from the system prompt, including the 6-field relation format with compact JSON Relation Context.
2.  **Focus on Corrections/Additions:**
    *   **Do NOT** re-output entities and relationships that were correctly extracted.
    *   If an entity or relationship was missed, extract it now.
    *   If a relation was extracted without Relation Context (only 5 fields), re-output it with a 6th field JSON.
3.  **Output Format — Entities:** 4 fields: `entity{tuple_delimiter}name{tuple_delimiter}type{tuple_delimiter}description`
4.  **Output Format — Relationships:** 6 fields: `relation{tuple_delimiter}src{tuple_delimiter}tgt{tuple_delimiter}keywords{tuple_delimiter}description{tuple_delimiter}RELATION_CONTEXT_JSON`
5.  **Output Content Only:** No introductory or concluding remarks.
6.  **Completion Signal:** Output `{completion_delimiter}` as the final line.
7.  **Output Language:** {language}. Proper nouns in original language.

<Output>
"""

# ─────────────────────────────────────────────────────────────────────────────
# JSON extraction (Step 4 — upstream 1.5.x alignment). Emits a single JSON object;
# relation_context is a first-class key. Braces are doubled for str.format().
# ─────────────────────────────────────────────────────────────────────────────

PROMPTS["cg_entity_extraction_json_system_prompt"] = """You are an expert knowledge-graph extractor. From the input text, extract entities and the relationships between them, and return a SINGLE JSON object — no prose, no markdown code fences.

Use these entity types: {entity_types}. Write all text in {language} (keep proper nouns in their original language).

Output exactly this schema:
{{
  "entities": [
    {{"entity_name": "<name, Title Case>", "entity_type": "<one of the types above>", "description": "<concise description grounded in the text>"}}
  ],
  "relationships": [
    {{
      "src_id": "<source entity name — must match an entity above>",
      "tgt_id": "<target entity name — must match an entity above>",
      "keywords": "<high-level relationship keywords, comma-separated>",
      "description": "<why these entities are related, grounded in the text>",
      "relation_context": {{
        "supporting_sentences": ["<verbatim quote from the text>"],
        "decision_trace": "<rationale / approval / exception / override>",
        "approved_by": "<approver name>",
        "approved_via": "<slack|zoom|email|in_person|jira|system>",
        "valid_from": "<YYYY-MM-DD>",
        "valid_until": "<YYYY-MM-DD>",
        "policy_ref": "<policy name or id>",
        "quantitative_data": "<numbers, amounts, percentages>",
        "temporal_info": "<validity period phrase>",
        "provenance": "<source reference>",
        "confidence_score": 0.0
      }}
    }}
  ]
}}

Rules:
- Include a relationship ONLY if both of its entities appear in "entities".
- ALWAYS include "relation_context" for every relationship, populated with at least "supporting_sentences" (a verbatim quote from the text) and "confidence_score" in [0,1]. Omit only the individual sub-fields the text does not support — never omit the whole "relation_context".
- Fill in EACH sub-field whenever its trigger is present in the text — do not settle for supporting_sentences + confidence_score when more is available. Triggers:
  * "quantitative_data": ANY number, amount, percentage, count, price, or measurement tied to the relationship (e.g. "21 theme nodes", "20% discount", "$1,499").
  * "decision_trace": ANY stated reason, rationale, justification, cause, purpose, hypothesis, or "why" behind the relationship — even a one-clause explanation.
  * "provenance": the source of the claim (document, section, channel, meeting, or example it comes from) whenever it is identifiable — fill this on nearly every relationship.
  * "temporal_info": any validity period, date range, or "as of" phrasing.
  * "approved_by" / "approved_via" / "policy_ref" / "valid_from" / "valid_until": explicit approvals, decision channels, policies, or validity dates.
  Aim to populate 3+ sub-fields per relationship when the text allows; a bare supporting_sentences + confidence_score is only acceptable when the text truly offers nothing more.
- Do NOT invent facts. Every value must be grounded in the input text.
- Exclude non-entities: file paths, code identifiers, environment-variable names, git hashes, bare numbers, and pronouns.
- Treat relationships as undirected unless the text states a direction.
- Return ONLY the JSON object.

Examples follow. Study how every relationship carries a relation_context — decision-rich relations fill the approval/policy fields; ordinary factual relations still carry supporting_sentences and confidence_score.

__EXAMPLES__
"""

PROMPTS["cg_entity_extraction_json_examples"] = [
    """<Input Text>
```
During the Q3 2024 business review, Sarah Chen (VP of Sales) approved a 20% discount for MegaCorp's enterprise deal, citing their five-year relationship and a competing offer from Salesforce. The discount was valid until December 31, 2024. This was discussed in the Slack channel #deals-review on August 14, 2024.
```

<Output>
{"entities": [{"entity_name": "Sarah Chen", "entity_type": "Person", "description": "Sarah Chen is the VP of Sales who approved a 20% discount for MegaCorp's enterprise deal during the Q3 2024 business review."}, {"entity_name": "MegaCorp", "entity_type": "Organization", "description": "MegaCorp is an enterprise client that received a 20% discount, citing a long-standing relationship and competitive pressure from Salesforce."}, {"entity_name": "Salesforce", "entity_type": "Organization", "description": "Salesforce is a competitor that made a competing offer to MegaCorp, influencing the discount approval."}], "relationships": [{"src_id": "Sarah Chen", "tgt_id": "MegaCorp", "keywords": "discount approval, deal negotiation", "description": "Sarah Chen approved a 20% discount for MegaCorp's enterprise deal during the Q3 2024 business review.", "relation_context": {"supporting_sentences": ["Sarah Chen (VP of Sales) approved a 20% discount for MegaCorp's enterprise deal"], "decision_trace": "Approved citing five-year relationship and competing offer from Salesforce", "approved_by": "Sarah Chen", "approved_via": "slack", "valid_until": "2024-12-31", "quantitative_data": "20% discount", "temporal_info": "Valid until December 31, 2024", "provenance": "Slack #deals-review, August 14, 2024", "confidence_score": 0.97}}, {"src_id": "MegaCorp", "tgt_id": "Salesforce", "keywords": "competitive pressure, market competition", "description": "Salesforce made a competing offer to MegaCorp that influenced the discount negotiation.", "relation_context": {"supporting_sentences": ["a competing offer from Salesforce"], "decision_trace": "Competing offer used as justification for discount approval", "temporal_info": "Q3 2024", "provenance": "Q3 2024 Business Review", "confidence_score": 0.88}}]}
""",
    """<Input Text>
```
Barack Obama served as the 44th President of the United States from January 20, 2009 to January 20, 2017. His administration passed the Affordable Care Act in 2010, expanding health insurance coverage to millions of Americans.
```

<Output>
{"entities": [{"entity_name": "Barack Obama", "entity_type": "Person", "description": "Barack Obama is the 44th President of the United States who served from 2009 to 2017 and oversaw the passage of the Affordable Care Act."}, {"entity_name": "United States", "entity_type": "Location", "description": "The United States is the country led by Barack Obama during his presidency from 2009 to 2017."}, {"entity_name": "Affordable Care Act", "entity_type": "Concept", "description": "The Affordable Care Act is landmark healthcare legislation passed in 2010 that expanded health insurance coverage to millions of Americans."}], "relationships": [{"src_id": "Barack Obama", "tgt_id": "United States", "keywords": "political leadership, presidency", "description": "Barack Obama served as the 44th President of the United States.", "relation_context": {"supporting_sentences": ["Barack Obama served as the 44th President of the United States from January 20, 2009 to January 20, 2017"], "temporal_info": "January 20, 2009 - January 20, 2017", "quantitative_data": "44th President", "confidence_score": 0.99}}, {"src_id": "Barack Obama", "tgt_id": "Affordable Care Act", "keywords": "legislation, policy achievement", "description": "Barack Obama's administration passed the Affordable Care Act in 2010, expanding healthcare coverage.", "relation_context": {"supporting_sentences": ["His administration passed the Affordable Care Act in 2010, expanding health insurance coverage to millions of Americans"], "decision_trace": "Policy goal to expand healthcare access", "temporal_info": "2010", "quantitative_data": "millions of Americans covered", "confidence_score": 0.98}}]}
""",
]

PROMPTS["cg_entity_extraction_json_user_prompt"] = """Extract entities and relationships from the following text as a single JSON object matching the schema.

Text:
{input_text}
"""

PROMPTS["cg_entity_extraction_json_continue_prompt"] = """Some entities and relationships may have been missed. Return a JSON object (same schema) containing ONLY entities and relationships NOT already extracted from the text below. If none remain, return {{"entities": [], "relationships": []}}.

Text:
{input_text}
"""

PROMPTS["cgr3_reason_prompt"] = """---Role---
You are a knowledge graph reasoning specialist performing iterative Retrieve-Rank-Reason analysis.

---Task---
Given a user query and retrieved context from a knowledge graph and document chunks, do TWO things:
1. Determine if the context is sufficient to give a comprehensive answer.
2. If sufficient, write a detailed answer. If not, identify exactly what's missing.

---Query---
{query}

---Retrieved Context---
{context}

---Instructions---
Analyze the retrieved context carefully. Consider entities, relationships, text chunks, and any relation context metadata (temporal info, decision traces, provenance).

Return ONLY a valid JSON object (no markdown, no explanation outside JSON):

If the context IS sufficient to answer comprehensively:
```
{{"is_sufficient": true, "answer": "<your detailed answer using specific facts, numbers, and names from the context>", "missing_info": null, "follow_up_entities": []}}
```

If the context is NOT sufficient:
```
{{"is_sufficient": false, "answer": null, "missing_info": "<specific description of what facts/details are missing>", "follow_up_entities": ["<entity or topic name 1>", "<entity or topic name 2>"]}}
```

Important:
- When answering, be thorough — include specific details, numbers, comparisons, and names from the context.
- When identifying missing info, be specific about what concepts or entities to search for next.
- follow_up_entities should be concrete nouns/names likely to exist in the knowledge graph.
- Err on the side of "sufficient" — if you have enough to give a useful answer, do so.

---Output---
"""
