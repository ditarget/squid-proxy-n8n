set -eu

COMPOSE_COMMAND=${COMPOSE_COMMAND:-docker compose}
ENGINE_COMMAND=${ENGINE_COMMAND:-docker}

compose_environment=$($COMPOSE_COMMAND config --environment)
configured_cidrs=$(printf '%s\n' "$compose_environment" | sed -n 's/^ALLOWED_CLIENT_CIDRS=//p')

if [ -z "$configured_cidrs" ] || [ "$configured_cidrs" = auto ]; then
    client_network=$(printf '%s\n' "$compose_environment" | sed -n 's/^CLIENT_NETWORK=//p')
    if [ -z "$client_network" ]; then
        printf '%s\n' 'CLIENT_NETWORK is required for automatic client subnets' >&2
        exit 1
    fi
    engine_executable=${ENGINE_COMMAND%% *}
    case "$engine_executable" in
        podman|*/podman) subnet_template='{{range .Subnets}}{{println .Subnet.String}}{{end}}' ;;
        *) subnet_template='{{range .IPAM.Config}}{{if .Subnet}}{{println .Subnet}}{{end}}{{end}}' ;;
    esac
    network_subnets=$($ENGINE_COMMAND network inspect --format "$subnet_template" -- "$client_network")
    ALLOWED_CLIENT_CIDRS=$(printf '%s\n' "$network_subnets" | awk 'NF {if (n++) printf ","; printf "%s", $0} END {printf "\n"}')
    if [ -z "$ALLOWED_CLIENT_CIDRS" ]; then
        printf '%s\n' 'CLIENT_NETWORK has no IPAM subnets; automatic client subnets cannot be resolved' >&2
        exit 1
    fi
    export ALLOWED_CLIENT_CIDRS
fi

exec $COMPOSE_COMMAND "$@"
