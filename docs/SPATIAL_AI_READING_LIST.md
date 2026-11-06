# Spatial AI: Cutting Edge Research Reading List

A curated guide to understanding spatial reasoning in AI systems - the path toward agents that can truly "see" and understand space.

---

## 1. Foundation: Why This Is Hard

**Start here to understand the problem:**

- **"On the Measure of Intelligence" - Francois Chollet (2019)**
  - Defines intelligence and why spatial reasoning is a core component
  - https://arxiv.org/abs/1911.01547

- **"Abstract Reasoning Challenge (ARC)"**
  - Benchmark showing LLMs fail at basic spatial/visual reasoning
  - https://github.com/fchollet/ARC

---

## 2. Vision-Language Models (Current State)

**What multimodal models can and cannot do:**

- **"GPT-4V(ision) System Card" - OpenAI (2023)**
  - Capabilities and limitations of vision-language models
  - https://openai.com/research/gpt-4v-system-card

- **"Gemini: A Family of Capable Multimodal Models" - Google (2023)**
  - State of the art in multimodal reasoning
  - https://arxiv.org/abs/2312.11805

- **"What's Left? Spatial Relations in Vision-Language Models" (2024)**
  - Systematic study of VLM failures on spatial reasoning
  - https://arxiv.org/abs/2310.03120

---

## 3. Embodied AI & Robotics

**Learning spatial understanding through physical interaction:**

- **"RT-2: Vision-Language-Action Models" - Google DeepMind (2023)**
  - Robots that understand language and space together
  - https://robotics-transformer2.github.io/
  - Key insight: spatial understanding emerges from physical grounding

- **"PaLM-E: An Embodied Multimodal Language Model" (2023)**
  - 562B parameter model trained on robotics + language
  - https://palm-e.github.io/

- **"Open X-Embodiment: Robotic Learning Datasets" (2023)**
  - Collaborative dataset for embodied AI
  - https://robotics-transformer-x.github.io/

---

## 4. 3D Scene Understanding

**Models that build internal 3D representations:**

- **"NeRF: Neural Radiance Fields" - Mildenhall et al. (2020)**
  - Foundational work on 3D scene representation
  - https://www.matthewtancik.com/nerf

- **"3D Gaussian Splatting for Real-Time Radiance Field Rendering" (2023)**
  - Faster, more practical 3D scene capture
  - https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/

- **"Segment Anything in 3D" - SAM3D (2023)**
  - 3D object understanding and segmentation
  - https://arxiv.org/abs/2304.12308

- **"LLaVA-3D: Towards Generalist 3D Vision-Language Models" (2024)**
  - LLMs that understand 3D scenes
  - Key direction for spatial reasoning

---

## 5. Geometric Deep Learning

**Neural architectures designed for spatial/geometric data:**

- **"Geometric Deep Learning: Grids, Groups, Graphs, Geodesics, and Gauges" (2021)**
  - Comprehensive textbook on geometric ML
  - https://geometricdeeplearning.com/
  - **Start with this for theoretical foundations**

- **"E(3)-Equivariant Graph Neural Networks" (2022)**
  - Networks that respect 3D geometric symmetries
  - Critical for spatial reasoning that generalizes

- **"Attention Is All You Need... For Graphs" - Graph Transformers**
  - Applying transformers to relational/spatial structures

---

## 6. Spatial Reasoning Benchmarks

**How we measure spatial understanding:**

- **"CLEVR: Compositional Language and Elementary Visual Reasoning" (2017)**
  - Classic benchmark for visual reasoning
  - https://cs.stanford.edu/people/jcjohns/clevr/

- **"PTR: A Benchmark for Part-based Conceptual, Relational, and Physical Reasoning" (2021)**
  - Tests spatial relationship understanding
  - https://arxiv.org/abs/2112.05136

- **"SpatialBench: Benchmarking Spatial Understanding" (2024)**
  - Recent benchmark specifically for spatial reasoning in LLMs
  - Tests: distance estimation, direction, containment, etc.

- **"VSR: Visual Spatial Reasoning" (2022)**
  - Tests understanding of "left of", "above", "between", etc.
  - https://arxiv.org/abs/2205.00363

---

## 7. World Models & Simulation

**Learning space through prediction:**

- **"World Models" - Ha & Schmidhuber (2018)**
  - Learning internal models of environments
  - https://worldmodels.github.io/

- **"Genie: Generative Interactive Environments" - Google DeepMind (2024)**
  - Learning world models from video that support interaction
  - https://sites.google.com/view/genie-2024

- **"Sora: Creating Video from Text" - OpenAI (2024)**
  - Video generation implicitly learns physics/space
  - https://openai.com/sora

---

## 8. Navigation & Mapping

**Spatial reasoning for moving through environments:**

- **"Learning to Navigate in Cities Without a Map" (2018)**
  - Deep RL for spatial navigation
  - https://arxiv.org/abs/1804.00168

- **"VLMaps: Visual Language Maps for Robot Navigation" (2023)**
  - Combining language understanding with spatial maps
  - https://vlmaps.github.io/

- **"SayNav: Grounding Large Language Models for Dynamic Planning" (2023)**
  - LLMs that reason about spatial navigation
  - https://arxiv.org/abs/2309.04077

---

## 9. Key Research Labs to Follow

- **Google DeepMind Robotics** - RT-X, PaLM-E, Genie
- **Berkeley AI Research (BAIR)** - Embodied AI, manipulation
- **Stanford Vision Lab** - 3D understanding, scene graphs
- **MIT CSAIL** - Geometric reasoning, intuitive physics
- **NVIDIA Research** - 3D vision, simulation
- **Meta AI (FAIR)** - Embodied agents, world models

---

## 10. Courses & Tutorials

- **CS231n: Deep Learning for Computer Vision (Stanford)**
  - Foundation in visual understanding
  - https://cs231n.stanford.edu/

- **CS 330: Deep Multi-Task and Meta Learning (Stanford)**
  - Learning to learn - relevant for spatial generalization
  - https://cs330.stanford.edu/

- **Geometric Deep Learning Course (2022)**
  - By Bronstein, Bruna, Cohen, Velickovic
  - https://geometricdeeplearning.com/lectures/

---

## Suggested Reading Order

**Week 1-2: Foundations**
1. Chollet - "On the Measure of Intelligence"
2. Try ARC benchmark yourself - see how hard spatial reasoning is
3. GPT-4V System Card - understand current limitations

**Week 3-4: Embodied AI**
4. RT-2 paper and demos
5. PaLM-E paper
6. Open X-Embodiment dataset overview

**Week 5-6: 3D Understanding**
7. NeRF original paper
8. 3D Gaussian Splatting
9. Play with 3D scene capture tools

**Week 7-8: Geometric Deep Learning**
10. Geometric Deep Learning textbook (chapters 1-4)
11. E(3)-Equivariant networks

**Week 9+: Cutting Edge**
12. World Models + Genie
13. Recent spatial reasoning benchmarks
14. Follow latest from DeepMind Robotics

---

## Key Insight

The path to spatial AI isn't just "better vision models" - it requires:
1. **Physical grounding** - learning from interaction, not just observation
2. **3D representations** - internal models of space, not just 2D patterns
3. **Geometric inductive biases** - architectures that respect spatial structure
4. **Predictive learning** - understanding space through prediction

Current LLMs + Vision are pattern matchers. True spatial cognition requires fundamentally different approaches.

---

*Last updated: May 2026*
