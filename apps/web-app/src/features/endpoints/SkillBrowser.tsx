import { useEffect, useRef, useState } from 'react'
import { getSkill, listSkills, type SkillDetail, type SkillSummary } from '../../api/skills'

export interface SkillBrowserProps {
  selected: readonly string[]
  disabled?: boolean
  onSelectedChange(selected: string[]): void
}

export default function SkillBrowser({ selected, disabled = false, onSelectedChange }: SkillBrowserProps) {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [detail, setDetail] = useState<SkillDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const listController = useRef<AbortController | null>(null)
  const detailController = useRef<AbortController | null>(null)
  const detailEpoch = useRef(0)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    listController.current = controller
    void listSkills(controller.signal).then(
      (items) => {
        if (!mounted.current || controller.signal.aborted) return
        setSkills([...items].sort((left, right) => left.name.localeCompare(right.name) || left.id.localeCompare(right.id)))
      },
      () => { if (mounted.current && !controller.signal.aborted) setError(true) },
    ).finally(() => { if (mounted.current && !controller.signal.aborted) setLoading(false) })
    return () => {
      mounted.current = false
      controller.abort()
      detailController.current?.abort()
      detailEpoch.current += 1
    }
  }, [])

  function toggle(id: string, checked: boolean) {
    const next = checked ? [...new Set([...selected, id])].sort() : selected.filter((item) => item !== id)
    if (next.length <= 32) onSelectedChange(next)
  }

  function openDetail(skill: SkillSummary) {
    detailController.current?.abort()
    const controller = new AbortController()
    detailController.current = controller
    const epoch = ++detailEpoch.current
    setDetail(null)
    void getSkill(skill.id, controller.signal).then(
      (value) => {
        if (mounted.current && !controller.signal.aborted && detailEpoch.current === epoch) setDetail(value)
      },
      () => { if (mounted.current && !controller.signal.aborted && detailEpoch.current === epoch) setError(true) },
    )
  }

  return (
    <section aria-labelledby="skill-browser-title">
      <h2 id="skill-browser-title">選擇 Skills</h2>
      <p>已選 {selected.length}／32</p>
      {loading && <p role="status">正在載入 Skills…</p>}
      {error && <p role="alert">目前無法載入 Skills，請稍後再試。</p>}
      <ul aria-label="Skills 清單">
        {skills.map((skill) => (
          <li key={skill.id}>
            <label htmlFor={`skill-${skill.id}`}>
              <input id={`skill-${skill.id}`} type="checkbox" checked={selected.includes(skill.id)} disabled={disabled}
                onChange={(event) => toggle(skill.id, event.target.checked)} />
              {skill.name} — {skill.description}
            </label>
            <button type="button" disabled={disabled} onClick={() => openDetail(skill)}>查看 {skill.name}</button>
          </li>
        ))}
      </ul>
      {detail && (
        <article aria-labelledby="skill-detail-title">
          <h3 id="skill-detail-title">{detail.name}</h3>
          <pre>{detail.content}</pre>
        </article>
      )}
    </section>
  )
}
