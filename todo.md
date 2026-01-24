
### Core Principles

- **Leave changes uncommited**: Do not commit/push/reset git tree.
- **Don't give up**: Provide comprehensive solutions
- **Fail fast**: Use assertions, throw errors early - no graceful fallbacks
- **Explicit over implicit**: No default parameters or optional chaining for required values
- **Type safety**: Use interfaces/classes/records/data classes, avoid tuples/any/dictionaries
- **SOLID**: Adhere to SOLID principles
- **RTFM**: Read documentation, code, and samples thoroughly, download docs when necessary:
  - Search for project documentation in `./docs` or `./.docs`
  - Read throug project readme
- Don't write obvious comments. Only write comments to explain something important
- Deliver sound, generic, universal solutions. Avoid workarounds.

### Code Style

- No magic constants - use named constants
- No backwards compatibility concerns - refactor freely
- Prefer composition over conditional logic
- Never duplicate, always generalize

### Project Structure

- Services: Use interface + implementation pattern when possible
- Always create and maintain reasonable .gitignore files

### Tasks

Before implementing anyting do:
    - read all contexts:
        - docs
        - TESTING.md
        - README.md
    - go through the tets to understand what it does

Refactor Mudyla python CLI application:
    - Move all formatters to dedicated `formatters` directory
    - Create a strict Formatters hierarchy:
        - OutputFormatter creates all other formatters and have the as a field
        - OutputFormatter on creation should create a Rich console for the current system following no_color option parameter
        - Basic usage: `output.print(output.context.format(abc))`
    - All formatters should use output.symbols for emojis, or use ascii symbols if output.support_emojis() = False
    - All formatters should return Rich Text class as a formatted string with Rich markdown tags
    - All formatters should ignore no_color option, as it should be handled by the top level Rich console configuration (all tags should be used by default, Rich console would remove them)
    - Create a modular function based execution, without long nesting implementations, create helpers to keep code simple

Refactor TaskTableManager:
    - Create a base class ActionLogger instead:
        - It should have 2 implemenations: raw and interactive table
        - interactive should work the same as current table manager
        - raw should only print action state changes, both should support color and no_color mode
        - raw should be used only if (simple_log or github_actions or verbose)
    - It should use OutputFormatter for printing and updating tables
    - It should use ActionKey/ContextId instead of formatted names, formatting should be done by the table itself
    - Use Rich table syntax highlighting where possible
    - Do not highlight logs, keep original formatting of the log files
    - Do not highlight empty meta/logs/ouptut/etc (`(output.json not found)` messages)

Refactor ExecutionEngine and all classes in engine.py:
    - It should use a complete ActionKey/ContextId, other Ids as a identifier through the code, stringified ids should be used only for output messages
    - It should use OutputFormatter everywhere, without custom prints
    - Create a modular function based execution, without long nesting implementations, create helpers to keep code simple
