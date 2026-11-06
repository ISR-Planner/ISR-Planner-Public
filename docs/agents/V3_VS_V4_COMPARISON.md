# v3 vs v4 Agent Comparison

> Historical note: this comparison predates the v4 production cutover. The active
> agent is now `server/agents/isr_agent_multi_v4.py`; v1-v3 are archived or
> compatibility-only.

## Current Status
- **Current Active Version**: v4 (`server/agents/isr_agent_multi_v4.py`)
- **Historical Context Below**: v3 was active and v4 was experimental when this document was written.

---

## Architecture Comparison

### v3: Task-Specialized Agents (Historical Active Version)
```
        COORDINATOR (Orchestrator)
              ↓
    ┌─────────┼─────────┐
    ↓         ↓         ↓
ALLOCATOR  ROUTER  VALIDATOR
                      ↓
                 OPTIMIZER
```

**Agent Count**: 5 agents  
**Focus**: Task decomposition - each agent handles a specific task  
**Communication**: Tool-based handoff via shared state  

### v4: Reasoning-Based Agents (Experimental)
```
        STRATEGIST (Analyzer)
              ↓
    ┌─────────┼─────────┐
    ↓         ↓         ↓
ALLOCATOR  ROUTE_OPT  CRITIC
                      ↓
                 RESPONDER
```

**Agent Count**: 5 agents  
**Focus**: Reasoning and explanation - agents explain WHY decisions were made  
**Communication**: Reasoning chain through shared state  

---

## Key Differences

### 1. **Agent Roles**

| Aspect | v3 | v4 |
|--------|----|----|
| **Top Agent** | Coordinator (routes tasks) | Strategist (analyzes & reasons) |
| **Allocation** | Allocator (execute) | Allocator (reason & explain) |
| **Routing** | Router (TSP solver) | Route Optimizer (solve + analyze) |
| **Validation** | Validator (check constraints) | Critic (review + suggest improvements) |
| **Optimization** | Optimizer (Insert/Swap/2opt) | *(Integrated into Route Optimizer)* |
| **Final Output** | Coordinator formats | Responder (concise reasoning) |

### 2. **Core Philosophy**

**v3: Task Execution**
- "What needs to be done?" → Execute
- Agents are **workers** that perform specific tasks
- Minimal reasoning, maximum execution
- Focus: Correctness and completeness

**v4: Reasoning & Explanation**
- "Why should we do this?" → Reason → Execute
- Agents are **advisors** that explain their decisions
- Extensive reasoning before action
- Focus: Transparency and understanding

### 3. **State Structure**

**v3 State (`MultiAgentState`):**
```python
class MultiAgentState(TypedDict):
    messages: list
    environment: Dict
    drone_configs: Dict
    current_sequences: Dict
    target_allocation: Dict      # Allocator output
    computed_routes: Dict        # Router output
    validation_results: Dict     # Validator output
    current_agent: str
    next_agent: str
    task_complete: bool
```

**v4 State (`MissionState`):**
```python
class MissionState(TypedDict):
    messages: list
    environment: Dict
    drone_configs: Dict
    user_request: str
    request_type: str            # "question", "optimize", "command"
    commands: List[Dict]         # Explicit commands
    strategy_analysis: str       # Strategist reasoning
    allocation_reasoning: str    # Allocator reasoning
    route_analysis: str          # Router reasoning
    critic_review: str           # Critic review
    suggestions: List[str]       # Improvements
    allocation: Dict
    routes: Dict
```

### 4. **Prompt Engineering**

**v3: Directive Prompts**
- Clear, concise instructions
- "You are the Allocator Agent. Use these tools..."
- Focus on what to do

**v4: Reasoning Prompts**
- Extensive context and reasoning guidelines
- "You are the Strategist. Analyze the request, consider trade-offs..."
- Focus on why and how to think

### 5. **Tool Usage**

**v3: Direct Tool Execution**
- Each agent has specific tools
- Tools execute immediately
- Minimal intermediate reasoning

**v4: Reasoning Before Tools**
- Agents analyze before calling tools
- Explain why tool is needed
- Can defer to later agents

### 6. **Additional v4 Features**

v4 includes several advanced features not in v3:

1. **Coordinator v4 Integration**
   - Deterministic pre-pass for intent detection
   - Policy rule application
   - Guardrails validation
   - Drone contracts

2. **Constraint Program Support**
   - Parsed constraint operations from `memory.constraints`
   - Sequencing hints
   - More structured constraint handling

3. **Request Type Classification**
   - "question" - Answer without solving
   - "optimize" - Find best solution
   - "command" - Execute specific directive

4. **Critic Agent**
   - Reviews solution quality
   - Suggests improvements
   - Proactive optimization recommendations

5. **Responder Agent**
   - Dedicated to final answer formatting
   - Concise reasoning summaries
   - Better user communication

---

## Performance Comparison

### v3 Strengths
✅ **Faster** - Direct task execution, minimal overhead  
✅ **Simpler** - Easier to debug and understand  
✅ **Proven** - Currently deployed and stable  
✅ **Efficient** - Less token usage (fewer reasoning steps)  
✅ **Reliable** - Deterministic behavior  

### v3 Weaknesses
❌ Limited explanation capability  
❌ No proactive suggestions  
❌ Black-box decision making  
❌ Can't answer "why" questions well  

### v4 Strengths
✅ **Transparent** - Explains all decisions  
✅ **Adaptive** - Reasons about trade-offs  
✅ **Educational** - Can teach users about planning  
✅ **Sophisticated** - Handles complex reasoning  
✅ **Proactive** - Suggests improvements  

### v4 Weaknesses
❌ Slower - More reasoning = more tokens  
❌ Complex - Harder to debug reasoning chains  
❌ Experimental - Not fully tested  
❌ Expensive - Higher API costs  
❌ Risk of over-thinking - May complicate simple tasks  

---

## Use Case Recommendations

### Use v3 When:
- Production deployment with reliability requirements
- Performance and speed are critical
- Simple, straightforward missions
- User just wants results, not explanations
- Budget-conscious (lower API costs)

### Use v4 When:
- Training scenarios (users learning ISR planning)
- Complex missions requiring trade-off analysis
- Users need explanations for decisions
- Research/experimentation with reasoning agents
- Transparency and auditability are important

---

## Example Interaction Comparison

### User: "Allocate targets efficiently"

**v3 Response:**
```
Coordinator: Routing to Allocator...
Allocator: Running efficient allocation...
Router: Computing routes...
Validator: All routes valid.
Optimizer: Optimized with Insert Missed.

Final Solution:
- D1: A1→T1→T3→A1 (45km, 8 pts)
- D2: A2→T2→T5→A2 (38km, 12 pts)
...
```

**v4 Response:**
```
Strategist: User requests efficient allocation. This is an optimization 
task. I need to analyze drone capabilities and target priorities...

Allocator: Reasoning about allocation:
- D1 can reach T1, T3 within fuel budget (150km)
- D2 is closer to T2, T5 and has spare capacity
- Efficient strategy maximizes priority/distance ratio
- Assigning T1, T3 to D1 yields 8 pts at 45km (0.18 ratio)
- Assigning T2, T5 to D2 yields 12 pts at 38km (0.32 ratio)

Route Optimizer: Computing optimal routes...
- D1 route distance: 45km (feasible with 150km budget)
- D2 route distance: 38km (feasible with 150km budget)

Critic: Reviewing solution:
- All high-priority targets covered
- Fuel margins adequate (D1: 70%, D2: 75%)
- Suggestion: T4 (priority 6) is unvisited but within D2's reach

Responder: Allocated targets efficiently. D1 covers T1,T3 (8 pts) and 
D2 covers T2,T5 (12 pts). Total 20 pts collected with comfortable fuel 
margins. T4 could be added to D2's route if desired.
```

---

## Migration Path (If Needed)

If you want to activate v4:

1. **Uncomment in main.py:**
   ```python
   from .agents.isr_agent_multi_v4 import run_multi_agent_v4
   ```

2. **Update endpoint to use v4:**
   ```python
   result = run_multi_agent_v4(
       user_request=req.request,
       env=req.env,
       drone_configs=req.drone_configs
   )
   ```

3. **Test thoroughly** before production use

---

## Recommendation for Mission Planner Integration

Given that we're adding a **Mission Planner Agent** for orchestration tools:

### Option A: Extend v3 (Recommended)
- ✅ Stable base
- ✅ Proven architecture
- ✅ Mission Planner adds orchestration without changing core
- ✅ Keeps v3's simplicity and speed

### Option B: Extend v4
- ✅ Better reasoning capabilities
- ✅ More natural for complex constraints
- ❌ Experimental base
- ❌ More complex to debug

### Option C: Create v5
- ✅ Clean slate
- ✅ Best of both worlds (v3's speed + v4's reasoning)
- ✅ Incorporate lessons learned
- ❌ More work upfront

**My Recommendation**: **Extend v3** to create v5 with Mission Planner agent. This gives us:
- Proven stability from v3
- New orchestration capabilities
- Room to incorporate v4's reasoning where beneficial
- Clean separation of concerns

---

## Summary

| Feature | v3 (Historical) | v4 (Now Active) |
|---------|-------------|-------------------|
| **Speed** | ⚡⚡⚡ Fast | ⚡⚡ Moderate |
| **Reasoning** | ⭐ Basic | ⭐⭐⭐ Extensive |
| **Transparency** | ⭐ Minimal | ⭐⭐⭐ High |
| **Complexity** | ⭐⭐ Moderate | ⭐⭐⭐ High |
| **Stability** | ✅ Proven | ⚠️ Experimental |
| **Cost** | 💰 Lower | 💰💰 Higher |
| **Use Case** | Production | Research/Training |

**Current Status**: this recommendation is superseded; v4 is the active runtime.
