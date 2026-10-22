# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- towncrier release notes start -->

## 0.1.0-beta (2026-10-21)

### Features

- Export the StratoNext workspaces that enabled the Port integration, and per workspace the environments, agents, projects, tasks, scheduled jobs and reports.
- Native Port actions `create_task`, `dispatch_task` and `cancel_task`.
- `/integration/tasks/*` webhook routes as a fallback for Port workflow WEBHOOK nodes.
