// Colours and small helpers shared by every component.
// Each palette was checked for colour-blind separation and contrast.

// Planting priority, low to high: one warm hue, getting darker
export const PRIORITY = ["#e99460", "#da7044", "#c55031", "#a03524", "#6f1d19"];
export const NO_DEMAND = "#c9c6bd";               // streets nobody walks on in the simulation
export const STREET_GREY = "#a9a69e";             // streets in route mode

export const FASTEST = "#e8743b";                 // sunny, fastest route (dashed)
export const SHADIEST = "#0b6e5f";                // shadiest route
export const SHADE_LIGHT = "#7fbcae";             // shade today, in the hourly bars

// One colour per area, in the order of areas.py, so an area keeps its colour
export const AREA_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

export const level = (score) => (score <= 0 ? 0 : Math.min(5, 1 + Math.floor(score / 20)));
export const levelColor = (score) => (level(score) === 0 ? NO_DEMAND : PRIORITY[level(score) - 1]);
export const LEVEL_NAMES = ["Lowest", "Low", "Medium", "High", "Highest"];

export const hourLabel = (h) => `${h % 12 || 12} ${h < 12 ? "AM" : "PM"}`;
export const shortHour = (h) => `${h % 12 || 12}${h < 12 ? "a" : "p"}`;
export const fmt = (n) => Math.round(n).toLocaleString("en-IN");
export const pct = (x) => `${Math.round(x * 100)}%`;
