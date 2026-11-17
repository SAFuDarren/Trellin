-- A public destination does not make a replayed prerequisite's type public.
-- Follow immutable local dependencies, including cross-claim replay origins.
CREATE FUNCTION rm.lean_model_packet_is_public(packet_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM formalization_packets packet JOIN claims claim ON claim.id = packet.claim_id
        WHERE packet.id = $1 AND claim.sensitivity = 'public'
    ) THEN
        RETURN false;
    END IF;
    PERFORM require_public_model_packet($1);
    RETURN true;
EXCEPTION WHEN check_violation THEN
    RETURN false;
END $$;

CREATE FUNCTION rm.lean_model_basis_is_public(version_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_public_model_basis(jsonb_build_array($1));
    RETURN true;
EXCEPTION WHEN check_violation THEN
    RETURN false;
END $$;

CREATE FUNCTION rm.public_lean_model_lemmas(anchor_id uuid) RETURNS TABLE (id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE
    roots AS MATERIALIZED (
        SELECT lemma.id
        FROM local_lean_lemmas lemma
        WHERE lemma.statement_id = $1
    ), ancestors(root_id, lemma_id) AS (
        SELECT id, id FROM roots
        UNION
        SELECT current.root_id, dep.prerequisite_id
        FROM ancestors current JOIN local_lean_lemma_dependencies dep ON dep.lemma_id = current.lemma_id
    ), lineage AS MATERIALIZED (
        SELECT DISTINCT ancestor.lemma_id, statement.packet_id
        FROM ancestors ancestor
        JOIN local_lean_lemmas lemma ON lemma.id = ancestor.lemma_id
        JOIN lean_statements statement ON statement.id = lemma.statement_id
    ), packets AS MATERIALIZED (
        SELECT discovered.packet_id, lean_model_packet_is_public(discovered.packet_id) AS visible
        FROM (SELECT DISTINCT packet_id FROM lineage) discovered
    ), used_basis AS MATERIALIZED (
        SELECT DISTINCT used.lemma_id, used.version_id
        FROM lineage JOIN local_lean_lemma_basis used USING (lemma_id)
    ), basis AS MATERIALIZED (
        SELECT discovered.version_id, lean_model_basis_is_public(discovered.version_id) AS visible
        FROM (SELECT DISTINCT version_id FROM used_basis) discovered
    ), concealed AS (
        SELECT item.lemma_id FROM lineage item JOIN packets packet USING (packet_id)
        WHERE NOT packet.visible
        UNION
        SELECT used.lemma_id FROM used_basis used JOIN basis item USING (version_id)
        WHERE NOT item.visible
    )
    SELECT root.id FROM roots root
    WHERE NOT EXISTS (
        SELECT FROM ancestors ancestor JOIN concealed hidden USING (lemma_id)
        WHERE ancestor.root_id = root.id
    )
    AND NOT EXISTS (
        SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = root.id
    )
$$;

REVOKE ALL ON FUNCTION rm.lean_model_packet_is_public(uuid), rm.lean_model_basis_is_public(uuid),
    rm.public_lean_model_lemmas(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.public_lean_model_lemmas(uuid) TO rm_verifier;
