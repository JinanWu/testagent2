import {
  ApiFormatError, apiRequest, boundedString, byteLength, encodedRoute, exactObject, type ApiRoute,
} from './client'

export interface SkillSummary { id: string; name: string; category: string; description: string }
export interface SkillDetail extends SkillSummary { content: string }

function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length <= maximum
}
function parseSummary(value: unknown): SkillSummary {
  const item = exactObject(value, ['id', 'name', 'category', 'description'])
  if (!item || !boundedString(item.id, 128) || !boundedString(item.name, 256) ||
      !text(item.category, 256) || !text(item.description, 4096)) throw new ApiFormatError()
  return { id: item.id, name: item.name, category: item.category, description: item.description }
}

export function buildSkillDetailRoute(id: string): ApiRoute {
  return encodedRoute('/api/skills/', id)
}
export async function listSkills(signal?: AbortSignal): Promise<SkillSummary[]> {
  const outer = exactObject(await apiRequest('/api/skills', { signal, expectedStatus: 200 }), ['skills'])
  if (!outer || !Array.isArray(outer.skills) || outer.skills.length > 10_000) throw new ApiFormatError()
  return outer.skills.map(parseSummary)
}
export async function getSkill(id: string, signal?: AbortSignal): Promise<SkillDetail> {
  const value = await apiRequest(buildSkillDetailRoute(id), { signal, expectedStatus: 200 })
  const item = exactObject(value, ['id', 'name', 'category', 'description', 'content'])
  if (!item || typeof item.content !== 'string' || byteLength(item.content) > 256 * 1024) throw new ApiFormatError()
  const summary = parseSummary({ id: item.id, name: item.name, category: item.category, description: item.description })
  return { ...summary, content: item.content }
}
