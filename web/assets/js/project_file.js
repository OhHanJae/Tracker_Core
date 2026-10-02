export const PROJECT_EXTENSION = '.tre';

export function ensureProjectFilename(filename) {
  const value = String(filename || '').trim() || 'trackeye_config';
  return value.toLowerCase().endsWith(PROJECT_EXTENSION) ? value : `${value}${PROJECT_EXTENSION}`;
}

export function hasProjectExtension(filename) {
  return String(filename || '').toLowerCase().endsWith(PROJECT_EXTENSION);
}

export function createProjectPayload(settings, modules, exportedAt = new Date().toISOString()) {
  return {
    schema_version: '1.1',
    exported_at: exportedAt,
    settings,
    modules,
    internal_policy: {
      plc_device_address_format: 'Dnnn',
      heartbeat: {
        enabled: true,
        mapping: 'PLC_HEARTBEAT / CONTROLLER_HEARTBEAT_RETURN (Data Map)',
        ui_editable: false,
      },
    },
  };
}

export function serializeProject(settings, modules, exportedAt) {
  return JSON.stringify(createProjectPayload(settings, modules, exportedAt), null, 2);
}

export function parseProject(text, filename) {
  if (!hasProjectExtension(filename)) throw new Error('project extension must be .tre');
  const project = JSON.parse(text);
  if (!Array.isArray(project.settings)) throw new Error('settings missing');
  return project;
}
