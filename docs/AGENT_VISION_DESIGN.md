# Agent Vision: Multimodal Spatial Perception

## Overview

Give the reasoning agent the ability to **see** the mission map visually, enabling intuitive spatial perception that raw coordinates cannot provide.

## The Problem

Currently, the agent receives spatial information as numbers:
```
T14: priority=8, pos=(33.7503, -84.1620), type=d
```

A human looking at the map instantly perceives:
- T14 is isolated far to the east
- The routes cross inefficiently
- There's a natural "sweep" through the southern targets

The agent cannot perceive these patterns from coordinates alone.

## The Solution

**Send the rendered map image to Claude's vision capabilities.**

Claude can then:
1. See the overall layout at a glance
2. Notice route crossings visually
3. Identify clusters and outliers
4. Perceive inefficient zigzag patterns
5. Suggest improvements based on what it *sees*

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (webapp/isr.js)                               │
│                                                         │
│  ┌─────────────────────┐                                │
│  │  Canvas Rendering   │──► canvas.toDataURL('image/png')
│  │  (map, routes, etc) │                                │
│  └─────────────────────┘                                │
└───────────────────┬─────────────────────────────────────┘
                    │ base64 PNG
                    ▼
┌─────────────────────────────────────────────────────────┐
│  Agent Endpoint                                         │
│                                                         │
│  POST /api/agents/reason                                │
│  {                                                      │
│    "request": "optimize the mission",                   │
│    "environment": {...},                                │
│    "drone_configs": {...},                              │
│    "map_image": "data:image/png;base64,..."  ◄── NEW    │
│  }                                                      │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│  Reasoning Agent                                        │
│                                                         │
│  System prompt now includes:                            │
│  - Text description of mission                          │
│  - **Visual map image** ◄── Claude sees this            │
│                                                         │
│  Agent can reason: "Looking at the map, I notice        │
│  D1's route crosses D2's path near the center.          │
│  Swapping T8 and T13 would eliminate this crossing."    │
└─────────────────────────────────────────────────────────┘
```

## Implementation Steps

### Phase 1: Canvas Capture (Frontend)

Add function to capture current canvas state:

```javascript
function captureMapImage() {
  const canvas = document.getElementById('canvas');
  // Render current state if needed
  renderCanvas();
  // Export as base64 PNG
  return canvas.toDataURL('image/png');
}
```

### Phase 2: Include in Agent Request (Frontend)

Modify `callReasoningAgent()` to include the image:

```javascript
async function callReasoningAgent(request) {
  const mapImage = captureMapImage();

  const response = await fetch('/api/agents/reason', {
    method: 'POST',
    body: JSON.stringify({
      request: request,
      environment: state.env,
      drone_configs: state.droneConfigs,
      map_image: mapImage  // NEW
    })
  });
}
```

### Phase 3: Agent Receives Image (Backend)

Modify `run_reasoning_agent()` to include image in context:

```python
from langchain_core.messages import HumanMessage

def run_reasoning_agent(request, environment, drone_configs, map_image=None):
    # Build message content
    content = []

    # Add text request
    content.append({"type": "text", "text": request})

    # Add map image if provided
    if map_image:
        # Strip data URL prefix if present
        if map_image.startswith("data:image"):
            map_image = map_image.split(",")[1]

        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": map_image
            }
        })

    # Create message with multimodal content
    message = HumanMessage(content=content)
```

### Phase 4: Update System Prompt

Add visual analysis guidance to agent prompt:

```
## Visual Analysis

You will receive a map image showing the current mission state:
- Blue triangles: Airports
- Colored circles: Targets (color = type, size = priority)
- Colored lines: Drone routes
- Red circles: SAM zones

When analyzing the map visually, look for:
- Route crossings (inefficient - consider target swaps)
- Zigzag patterns (poor sequencing)
- Isolated targets (may need special handling)
- Clustered targets (natural groupings)
- SAM zone impacts on routes

Combine your visual perception with numerical analysis for optimal solutions.
```

## Usage Scenarios

### Scenario 1: Initial Planning
User: "Plan this mission"
Agent: *sees the map* "I notice the targets form two distinct clusters - northern targets near A2, southern targets near A1. The geographic allocation naturally follows this split..."

### Scenario 2: Optimization Review
User: "Can this be improved?"
Agent: *sees the routes* "Looking at the current solution, D1's route makes a sharp U-turn at T8. Reordering to visit T7 before T8 would create a smoother arc..."

### Scenario 3: Crossing Detection
User: "Check for problems"
Agent: *sees crossings* "I can see D1 and D2's paths cross between T13 and T8. If D1 took T13 and D2 took T8, both routes would be more direct..."

## Benefits

1. **Intuitive perception** - Agent sees patterns humans see
2. **No rule explosion** - Don't need to code every geometric check
3. **Natural reasoning** - Agent explains what it sees in human terms
4. **Emergent insights** - May notice things we didn't think to check for

## Considerations

- **Image size**: Keep canvas reasonable (1000x800 typical)
- **Rendering quality**: Ensure labels are readable
- **Token cost**: Images add to context, but provide high value
- **Latency**: Base64 encoding adds minimal overhead

## Future Enhancements

1. **Annotated images** - Highlight specific areas of concern
2. **Before/after comparison** - Show original vs optimized
3. **Animation frames** - Show temporal progression
4. **Zoom regions** - Focus on problem areas
