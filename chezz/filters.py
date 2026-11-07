"""Library filters shared by the game list, weakness profile and stats (SQL over games alias g)."""


def game_filters(
    date_from: str | None = None,
    date_to: str | None = None,
    color: str | None = None,
    result: str | None = None,
    time_class: str | None = None,
    opening: str | None = None,
    tag: str | None = None,
    starred: bool = False,
    q: str | None = None,
) -> tuple[str, list]:
    clauses, params = [], []
    if date_from:
        clauses.append("g.date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("g.date <= ?")
        params.append(date_to + "~")  # '~' sorts after digits, so the whole day is included
    if color:
        clauses.append("g.user_color = ?")
        params.append(color)
    if result:
        clauses.append("g.user_result = ?")
        params.append(result)
    if time_class:
        clauses.append("g.time_class = ?")
        params.append(time_class)
    if opening:
        clauses.append("g.opening_family = ?")
        params.append(opening)
    if tag:
        clauses.append("EXISTS (SELECT 1 FROM game_tags t WHERE t.game_id = g.id AND t.tag = ?)")
        params.append(tag)
    if starred:
        clauses.append("g.starred = 1")
    if q:
        like = f"%{q}%"
        clauses.append("(g.white LIKE ? OR g.black LIKE ? OR g.opening LIKE ? OR g.event LIKE ? OR g.notes LIKE ?)")
        params += [like] * 5
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params
