#!/usr/bin/env bash
# Create all AWS resources NetPulse needs. Run in AWS CloudShell:
#   curl -fsSL https://raw.githubusercontent.com/ZXithunf/networkapp/main/scripts/aws-setup.sh | bash
#
# Safe to re-run: anything that already exists is reused.
# Creates: ECR repo, EC2 instance role + profile, security group (port 80 only),
#          t3.micro Amazon Linux 2023 instance with Docker, GitHub OIDC provider,
#          and a least-privilege IAM role that GitHub Actions assumes.
# Remove everything again with scripts/aws-teardown.sh.
set -euo pipefail

GH_REPO="${GH_REPO:-ZXithunf/networkapp}"
REPO="netpulse"            # ECR repository
NAME="netpulse"            # instance Name tag
EC2_ROLE="netpulse-ec2"
GH_ROLE="netpulse-github"
SG_NAME="netpulse-web"

export AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
[ -n "$AWS_REGION" ] || { echo "Set AWS_REGION first, e.g. export AWS_REGION=eu-west-2"; exit 1; }
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=""
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "==> Account $ACCOUNT_ID, region $AWS_REGION, GitHub repo $GH_REPO"

# 1. ECR repository ----------------------------------------------------------
if ! aws ecr describe-repositories --repository-names "$REPO" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "$REPO" \
    --image-tag-mutability IMMUTABLE \
    --image-scanning-configuration scanOnPush=true >/dev/null
fi
echo "==> ECR repository: $REPO"

# 2. Instance role: SSM (deploys without SSH) + ECR read (pull images) --------
if ! aws iam get-role --role-name "$EC2_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$EC2_ROLE" --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                   "Action": "sts:AssumeRole"}]}' >/dev/null
fi
aws iam attach-role-policy --role-name "$EC2_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam attach-role-policy --role-name "$EC2_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
if ! aws iam get-instance-profile --instance-profile-name "$EC2_ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$EC2_ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$EC2_ROLE" --role-name "$EC2_ROLE"
fi
echo "==> Instance role: $EC2_ROLE"

# 3. Security group: HTTP in, nothing else (no SSH) --------------------------
VPC_ID="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)"
if [ "$VPC_ID" = "None" ]; then
  VPC_ID="$(aws ec2 create-default-vpc --query Vpc.VpcId --output text)"
fi
SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [ "$SG_ID" = "None" ]; then
  SG_ID="$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "NetPulse HTTP" --vpc-id "$VPC_ID" --query GroupId --output text)"
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
    --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
fi
echo "==> Security group: $SG_ID (port 80 only)"

# 4. EC2 instance: Amazon Linux 2023 + Docker, IMDSv2 only ------------------
INSTANCE_ID="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)"
if [ "$INSTANCE_ID" = "None" ]; then
  AMI_ID="$(aws ssm get-parameter \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query Parameter.Value --output text)"
  USER_DATA="$(printf '#!/bin/bash\ndnf install -y docker\nsystemctl enable --now docker\n')"
  # A brand-new instance profile can take a few seconds to become usable.
  for attempt in 1 2 3 4 5 6; do
    if INSTANCE_ID="$(aws ec2 run-instances \
        --image-id "$AMI_ID" --instance-type t3.micro \
        --security-group-ids "$SG_ID" \
        --iam-instance-profile "Name=$EC2_ROLE" \
        --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1,HttpEndpoint=enabled \
        --user-data "$USER_DATA" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
        --query 'Instances[0].InstanceId' --output text 2>/tmp/netpulse-run.err)"; then
      break
    fi
    if [ "$attempt" = 6 ]; then cat /tmp/netpulse-run.err; exit 1; fi
    echo "    instance profile not ready yet, retrying in 10s..."
    sleep 10
  done
fi
aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 || true
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
echo "==> Instance: $INSTANCE_ID (running)"

# 5. GitHub OIDC provider (lets Actions get short-lived AWS credentials) -----
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null
fi
echo "==> OIDC provider: token.actions.githubusercontent.com"

# 6. Role GitHub Actions assumes: only this repo's main branch / production env
TRUST="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "$OIDC_ARN"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": [
        "repo:${GH_REPO}:ref:refs/heads/main",
        "repo:${GH_REPO}:environment:production"
      ]}
    }
  }]
}
JSON
)"
POLICY="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Effect": "Allow",
     "Action": ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
                "ecr:BatchGetImage"],
     "Resource": "arn:aws:ecr:${AWS_REGION}:${ACCOUNT_ID}:repository/${REPO}"},
    {"Effect": "Allow", "Action": "ssm:SendCommand",
     "Resource": ["arn:aws:ssm:${AWS_REGION}::document/AWS-RunShellScript",
                  "arn:aws:ec2:${AWS_REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}"]},
    {"Effect": "Allow", "Action": "ssm:GetCommandInvocation", "Resource": "*"}
  ]
}
JSON
)"
if aws iam get-role --role-name "$GH_ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$GH_ROLE" --policy-document "$TRUST"
else
  aws iam create-role --role-name "$GH_ROLE" --assume-role-policy-document "$TRUST" >/dev/null
fi
aws iam put-role-policy --role-name "$GH_ROLE" --policy-name netpulse-deploy --policy-document "$POLICY"
ROLE_ARN="$(aws iam get-role --role-name "$GH_ROLE" --query Role.Arn --output text)"
echo "==> GitHub role: $ROLE_ARN"

# 7. Wait for the instance to register with SSM (needed for deploys) --------
echo "==> Waiting for the instance to come online in SSM (1-3 minutes)..."
for _ in $(seq 1 40); do
  PING="$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
  [ "$PING" = "Online" ] && break
  sleep 10
done
[ "$PING" = "Online" ] || echo "WARNING: instance is not Online in SSM yet; wait a few minutes before deploying."

PUBLIC_IP="$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"

cat <<DONE

============================================================
 Done. Add these as GitHub repository VARIABLES (not secrets):
 https://github.com/${GH_REPO}/settings/variables/actions

   AWS_REGION        ${AWS_REGION}
   AWS_ROLE_ARN      ${ROLE_ARN}
   ECR_REPOSITORY    ${REPO}
   EC2_INSTANCE_ID   ${INSTANCE_ID}

 Then re-run the latest "CI/CD" workflow in the Actions tab.
 The app will be at:  http://${PUBLIC_IP}
 Remove everything:   curl -fsSL https://raw.githubusercontent.com/${GH_REPO}/main/scripts/aws-teardown.sh | bash
============================================================
DONE
