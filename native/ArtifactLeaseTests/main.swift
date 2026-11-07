import Foundation
import Darwin
// Synthetic executable: only the artifact lock is exercised, no device frameworks.
do {
    let lease=try CaptureArtifactLease(executable:CommandLine.arguments[1])
    withExtendedLifetime(lease) { print("held");fflush(stdout);_ = readLine() }
} catch { print("blocked");exit(1) }
