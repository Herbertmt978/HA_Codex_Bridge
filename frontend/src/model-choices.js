export function modelChoices(context, selected = "") {
  const options = [["", context.defaultModel ? `Inherit (${context.defaultModel})` : "Inherit default"]];
  for (const record of context.models || []) options.push([record.model, record.display_name || record.model]);
  if (selected && !options.some(([key]) => key === selected)) options.push([selected, `${selected} (saved, unavailable)`]);
  return options;
}

export function reasoningChoices(context, model = "", selected = "") {
  const record = context.models?.find((item) => item.model === (model || context.defaultModel));
  const options = [["", "Inherit default"]];
  for (const level of record?.thinking_levels || []) options.push([level, level.charAt(0).toUpperCase() + level.slice(1)]);
  if (selected && !options.some(([key]) => key === selected)) options.push([selected, `${selected} (saved, unavailable)`]);
  return options;
}
