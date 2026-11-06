// Presentation hint only. WebKit and the server enforce the actual boundaries.
export function embeddedHost(scope: unknown = globalThis): boolean {
  if (!scope || typeof scope !== "object") return false;
  const value = (scope as { __TINGJI_HOST__?: unknown }).__TINGJI_HOST__;
  return (
    !!value &&
    typeof value === "object" &&
    (value as { version?: unknown }).version === 1 &&
    (value as { embedded?: unknown }).embedded === true
  );
}
export const browserCaptureAllowed = () => !embeddedHost();
