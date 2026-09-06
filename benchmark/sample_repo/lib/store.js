const fs = require("fs");
const os = require("os");
const path = require("path");

const STORE_PATH = path.join(os.homedir(), ".todo-cli", "tasks.json");

function loadTasks() {
  if (!fs.existsSync(STORE_PATH)) return [];
  return JSON.parse(fs.readFileSync(STORE_PATH, "utf8"));
}

function saveTasks(tasks) {
  fs.mkdirSync(path.dirname(STORE_PATH), { recursive: true });
  fs.writeFileSync(STORE_PATH, JSON.stringify(tasks, null, 2));
}

function addTask(text) {
  const tasks = loadTasks();
  tasks.push({ id: tasks.length + 1, text, done: false });
  saveTasks(tasks);
}

function listTasks() {
  loadTasks().forEach((t) => console.log(`${t.id}. [${t.done ? "x" : " "}] ${t.text}`));
}

function completeTask(id) {
  const tasks = loadTasks();
  const task = tasks.find((t) => t.id === Number(id));
  if (task) task.done = true;
  saveTasks(tasks);
}

function removeTask(id, force) {
  if (!force) {
    console.log("Are you sure? Pass --force to skip this prompt.");
    return;
  }
  const tasks = loadTasks().filter((t) => t.id !== Number(id));
  saveTasks(tasks);
}

module.exports = { addTask, listTasks, completeTask, removeTask };
