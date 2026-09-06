#!/usr/bin/env node
const { Command } = require("commander");
const store = require("./lib/store");

const program = new Command();

program
  .command("add <text>")
  .description("add a new task")
  .action((text) => store.addTask(text));

program
  .command("list")
  .description("list all tasks")
  .action(() => store.listTasks());

program
  .command("done <id>")
  .description("mark a task as done")
  .action((id) => store.completeTask(id));

program
  .command("remove <id>")
  .description("remove a task")
  .option("--force", "skip confirmation prompt")
  .action((id, options) => store.removeTask(id, options.force));

program.parse();
