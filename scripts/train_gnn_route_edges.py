#!/usr/bin/env python3
"""Train the PyTorch ISR GNN route-edge next-waypoint model from JSONL."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.gnn.torch_gnn import (
    load_records,
    save_checkpoint,
    train_route_edge_model,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train torch_gnn_route_edge_v1 from route labels.")
    parser.add_argument("inputs", nargs="+", help="Input JSONL files.")
    parser.add_argument("--output", default="models/gnn_route_edge_v1.pt")
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--message-steps", type=int, default=2)
    parser.add_argument("--device", default="cpu", help="Torch device, e.g. cpu or cuda.")
    parser.add_argument("--seed", type=int, default=7)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_records(args.inputs)
    checkpoint = train_route_edge_model(
        records,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        hidden_dim=args.hidden_dim,
        message_steps=args.message_steps,
        seed=args.seed,
        device=args.device,
    )
    save_checkpoint(checkpoint, args.output)
    losses = checkpoint.get("losses", [])
    print(f"trained {checkpoint['version']} on {checkpoint['examples']} route transitions")
    print(f"loss: start={losses[0] if losses else '?'} end={losses[-1] if losses else '?'}")
    print(f"checkpoint: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
