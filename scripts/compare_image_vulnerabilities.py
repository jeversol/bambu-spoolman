"""Enforce absolute or PR-differential policy on complete Trivy image reports."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

EXPECTED_TYPES = {"alpine", "node-pkg", "python-pkg"}
SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


@dataclass(frozen=True, order=True)
class Finding:
    ecosystem: str
    scope: str
    package: str
    version: str
    advisory: str
    severity: str


@dataclass(frozen=True)
class Report:
    scanner_version: str
    findings: frozenset[Finding]


def required_text(value, description):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid {description}")
    return value


def parse_report(data):
    if not isinstance(data, dict) or data.get("SchemaVersion") != 2:
        raise ValueError("Expected a Trivy schema 2 report")
    if data.get("ArtifactType") != "container_image":
        raise ValueError("Expected a container image report")
    scanner = data.get("Trivy")
    if not isinstance(scanner, dict):
        raise ValueError("Missing scanner metadata")
    scanner_version = required_text(scanner.get("Version"), "scanner version")
    metadata = data.get("Metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("OS"), dict):
        raise ValueError("Missing operating-system metadata")
    os_info = metadata["OS"]
    if os_info.get("Family") != "alpine" or os_info.get("EOSL"):
        raise ValueError("Expected a supported Alpine image")
    os_version = required_text(os_info.get("Name"), "Alpine version")
    results = data.get("Results")
    if not isinstance(results, list) or not results:
        raise ValueError("Missing scan results")

    findings = set()
    scanned_types = set()
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Invalid scan result")
        ecosystem = required_text(result.get("Type"), "package ecosystem")
        target = required_text(result.get("Target"), "scan target")
        packages = result.get("Packages")
        if not isinstance(packages, list) or not packages:
            raise ValueError(f"Missing package inventory for {ecosystem}")
        inventory = set()
        for package in packages:
            if not isinstance(package, dict):
                raise ValueError("Invalid package inventory entry")
            inventory.add(
                (
                    required_text(package.get("Name"), "package name"),
                    required_text(package.get("Version"), "package version"),
                )
            )
        scanned_types.add(ecosystem)
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            raise ValueError("Invalid vulnerability list")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise ValueError("Invalid vulnerability entry")
            severity = vulnerability.get("Severity")
            if not isinstance(severity, str) or severity not in SEVERITIES:
                raise ValueError("Missing or invalid vulnerability severity")
            name = required_text(vulnerability.get("PkgName"), "affected package")
            version = required_text(
                vulnerability.get("InstalledVersion"), "affected version"
            )
            advisory = required_text(vulnerability.get("VulnerabilityID"), "advisory")
            if (name, version) not in inventory:
                raise ValueError("Finding is absent from the scanned package inventory")
            if severity not in {"HIGH", "CRITICAL"}:
                continue
            # OS Target contains the image tag, which differs for base/candidate.
            scope = f"alpine:{os_version}" if ecosystem == "alpine" else target
            package_path = vulnerability.get("PkgPath", "")
            if not isinstance(package_path, str):
                raise ValueError("Invalid affected package path")
            findings.add(
                Finding(
                    ecosystem,
                    f"{scope}:{package_path}",
                    name,
                    version,
                    advisory,
                    severity,
                )
            )
    if not EXPECTED_TYPES.issubset(scanned_types):
        raise ValueError("Incomplete OS, Node, or Python vulnerability coverage")
    return Report(scanner_version, frozenset(findings))


def compare_reports(candidate, baseline=None):
    if baseline is not None and candidate.scanner_version != baseline.scanner_version:
        raise ValueError("Baseline and candidate scanner versions differ")
    previous = baseline.findings if baseline is not None else frozenset()
    return {
        "introduced": candidate.findings - previous,
        "existing": candidate.findings & previous,
        "resolved": previous - candidate.findings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    try:
        candidate = parse_report(json.loads(args.candidate.read_text()))
        baseline = (
            parse_report(json.loads(args.baseline.read_text()))
            if args.baseline is not None
            else None
        )
        comparison = compare_reports(candidate, baseline)
        mode = "PR comparison" if baseline is not None else "absolute gate"
        lines = [f"## Image vulnerabilities ({mode})", ""]
        for category, findings in comparison.items():
            lines.append(
                f"{category.capitalize()}: {len(findings)} high/critical findings"
            )
            for finding in sorted(findings):
                # JSON escaping keeps package names and paths on one log line.
                lines.append(f"- {json.dumps(finding.__dict__, sort_keys=True)}")
        if comparison["existing"]:
            lines += ["", "Existing findings remain tracked and still block releases."]
        status = int(bool(comparison["introduced"]))
    except (OSError, ValueError) as error:
        lines = [f"Image vulnerability check failed: {error}"]
        status = 2
    message = "\n".join(lines) + "\n"
    print(message, end="")
    if args.summary is not None:
        with args.summary.open("a") as summary:
            summary.write(message)
    return status


if __name__ == "__main__":
    sys.exit(main())
