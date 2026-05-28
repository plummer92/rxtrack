# RXTrack Multi-Hospital Tenancy Plan

This is the foundation for turning RXTrack from a single Streamlit dashboard into a secure multi-hospital platform.

## Product Shape

```text
rxtrack.com
  Public shell site: brand, product overview, request access.

app.rxtrack.com
  Logged-in hospital workspace.

api.rxtrack.com
  Future backend API for custom frontend pages.
```

Preferred production domain: `rxtrack.com`.

If the primary domain is not immediately available, the product architecture still works with a temporary staging URL or an alternate domain until acquisition is complete.

## Core Concepts

`organizations`
: The top-level customer account. Usually a hospital, health system, or pharmacy operation.

`users`
: People who can sign in.

`organization_memberships`
: Connects users to organizations and defines their role.

`audit_logs`
: Tracks important actions for accountability.

## Roles

Initial roles should stay simple:

```text
owner
admin
manager
staff
viewer
```

Recommended permissions:

```text
owner    Manage billing, users, settings, all data
admin    Manage users, mappings, uploads, operational data
manager  View dashboards, upload reports, resolve discrepancies
staff    Use assigned workflow tools
viewer   Read-only access
```

## Tenant Isolation Rule

Every hospital-scoped query must be filtered by the logged-in user's organization:

```sql
SELECT *
FROM events
WHERE organization_id = :current_organization_id;
```

Do not trust frontend filters alone. The backend query itself must include tenant filtering.

## Migration Strategy

Phase 1 creates the foundation tables only. Existing operational tables keep working unchanged.

Phase 2 adds nullable `organization_id` columns to operational tables.

Phase 3 backfills existing rows into a default organization.

Phase 4 makes app queries tenant-aware.

Phase 5 makes `organization_id` required for new writes.

## Operational Tables To Tenant-Scope

These tables should eventually receive `organization_id`:

```text
events
config_events
med_costs
pharmacy_orders
staff_schedule
attendance_punches
inventory_audit
inventory_detailed
cycle_count_status
```

Additional app-specific tables can be added as we map the full schema.

## Safety Notes

1. Do not add strict `NOT NULL` constraints until every write path supplies `organization_id`.
2. Keep the current single-hospital behavior working during the transition.
3. Add tenant filtering before adding multiple real hospitals.
4. Add audit logs before allowing hospital admins to manage users or mappings.
5. Treat cost data, employee data, and medication workflow data as sensitive even when it is not PHI.
