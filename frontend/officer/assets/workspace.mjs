// Destinations are fixed and chosen only from the verified /auth/me response.
export function staffWorkspace(user) {
  const roles=new Set(user.roles.map(role=>role.code));
  const permissions=new Set(user.permissions.map(permission=>permission.code));
  if ((roles.has('CHARGE_OFFICER') || roles.has('STATION_COMMANDER')) && permissions.has('complaint.view_station')) return '/officer/';
  if (roles.has('NCC_OFFICER') && permissions.has('refusal.escalation.view')) return '/officer/';
  if (roles.has('INVESTIGATING_OFFICER') && permissions.has('docket.view_assigned')) return '/investigator/';
  return null;
}
