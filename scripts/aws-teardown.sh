#!/usr/bin/env bash
# Delete every AWS resource created by aws-setup.sh, so nothing keeps using credits.
# Run in AWS CloudShell (same region as setup):
#   curl -fsSL https://raw.githubusercontent.com/ZXithunf/networkapp/main/scripts/aws-teardown.sh | bash
#
# Safe to re-run: anything already gone is skipped.
set -uo pipefail   # no -e: keep going so one missing resource doesn't stop the cleanup

NAME="netpulse"; REPO="netpulse"; EC2_ROLE="netpulse-ec2"; GH_ROLE="netpulse-github"
SG_NAME="netpulse-web"

export AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
[ -n "$AWS_REGION" ] || { echo "Set AWS_REGION first, e.g. export AWS_REGION=eu-west-2"; exit 1; }
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=""
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" || exit 1
echo "==> Tearing down NetPulse in account $ACCOUNT_ID, region $AWS_REGION"

# 1. Instance (its disk is deleted with it) ----------------------------------
IDS="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [ -n "$IDS" ]; then
  # shellcheck disable=SC2086  # IDS is a space-separated list on purpose
  aws ec2 terminate-instances --instance-ids $IDS >/dev/null
  echo "==> Terminating $IDS (about a minute)..."
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --instance-ids $IDS
fi
echo "==> Instance: gone"

# 2. Security group (only deletable once the instance is gone) ---------------
for SG_ID in $(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[].GroupId' --output text); do
  aws ec2 delete-security-group --group-id "$SG_ID" && echo "==> Security group $SG_ID deleted"
done

# 3. ECR repository and all its images ---------------------------------------
aws ecr delete-repository --repository-name "$REPO" --force >/dev/null 2>&1 \
  && echo "==> ECR repository deleted" || echo "==> ECR repository: already gone"

# 4. GitHub Actions role ------------------------------------------------------
aws iam delete-role-policy --role-name "$GH_ROLE" --policy-name netpulse-deploy 2>/dev/null
aws iam delete-role --role-name "$GH_ROLE" 2>/dev/null \
  && echo "==> Role $GH_ROLE deleted" || echo "==> Role $GH_ROLE: already gone"

# 5. Instance role and profile -----------------------------------------------
aws iam remove-role-from-instance-profile --instance-profile-name "$EC2_ROLE" \
  --role-name "$EC2_ROLE" 2>/dev/null
aws iam delete-instance-profile --instance-profile-name "$EC2_ROLE" 2>/dev/null
for P in AmazonSSMManagedInstanceCore AmazonEC2ContainerRegistryReadOnly; do
  aws iam detach-role-policy --role-name "$EC2_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/$P" 2>/dev/null
done
aws iam delete-role --role-name "$EC2_ROLE" 2>/dev/null \
  && echo "==> Role $EC2_ROLE deleted" || echo "==> Role $EC2_ROLE: already gone"

# 6. GitHub OIDC provider (free, but remove it unless other projects use it) -
if [ "${KEEP_OIDC:-0}" != "1" ]; then
  aws iam delete-open-id-connect-provider --open-id-connect-provider-arn \
    "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com" 2>/dev/null \
    && echo "==> OIDC provider deleted" || echo "==> OIDC provider: already gone"
fi

echo
echo "Done. Nothing from NetPulse is left running in $AWS_REGION."
echo "Also delete the 4 variables at https://github.com/ZXithunf/networkapp/settings/variables/actions"
echo "so the CI/CD workflow stops trying to deploy."
