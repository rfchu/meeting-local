// Serialize renew/release across effect restarts, including a pending request at stop.
const requests = new Map<string, Promise<unknown>>();
export function queueAwake<T>(
  rid: string,
  operation: () => Promise<T>,
): Promise<T> {
  const next = (requests.get(rid) || Promise.resolve())
    .catch(() => {})
    .then(operation);
  requests.set(rid, next);
  void next
    .finally(() => {
      if (requests.get(rid) === next) requests.delete(rid);
    })
    .catch(() => {});
  return next;
}
