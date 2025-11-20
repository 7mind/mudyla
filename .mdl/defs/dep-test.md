# Dependency Declaration Tests

# action: task-a

Simple task that creates a file.

## definition

```bash
echo "Task A" > task-a.txt
ret result:string="Task A completed"
```

# action: task-b

Depends on task-a using dep command.

## definition

```bash
dep action.task-a

echo "Task B (depends on A)" > task-b.txt
ret result:string="Task B completed"
```

# action: task-c

Depends on both task-a and task-b using dep commands.

## definition

```bash
dep action.task-a
dep action.task-b

echo "Task C (depends on A and B)" > task-c.txt
ret result:string="Task C completed"
```
