"""Real-service integrations (Gmail, Slack, payments) for the live demo.

Each module falls back to the simulated Acme world in demo/env.py when its
credentials are missing, so the offline demo and tests never need them.
"""
