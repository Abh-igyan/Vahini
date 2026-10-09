import boto3
import time
import asyncio

ECS_CLUSTER = "vahini-fargate-cluster"
TASK_FAMILY = "vahini-go-worker"
SUBNET_ID = "subnet-0e6ca9f7981535cdb" # Using ap-south-1c
SECURITY_GROUP = "sg-01bc63c2a74b73d6f"
REGION = "ap-south-1"

ecs_client = boto3.client("ecs", region_name=REGION)
ec2_client = boto3.client("ec2", region_name=REGION)

def spawn_workers(num_workers: int):
    """Spawns ECS tasks and returns their ARNs"""
    print(f"Spawning {num_workers} ECS Fargate tasks...")
    response = ecs_client.run_task(
        cluster=ECS_CLUSTER,
        taskDefinition=TASK_FAMILY,
        count=num_workers,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [SUBNET_ID],
                "securityGroups": [SECURITY_GROUP],
                "assignPublicIp": "ENABLED"
            }
        }
    )
    
    tasks = response.get("tasks", [])
    task_arns = [task["taskArn"] for task in tasks]
    print(f"Spawned tasks: {task_arns}")
    return task_arns

async def wait_for_tasks_and_get_ips(task_arns):
    """Waits for tasks to reach RUNNING state and returns their public IPs"""
    print("Waiting for tasks to reach RUNNING state...")
    
    # We can use a thread to wait, since wait() is blocking
    def _wait():
        waiter = ecs_client.get_waiter('tasks_running')
        waiter.wait(
            cluster=ECS_CLUSTER,
            tasks=task_arns,
            WaiterConfig={'Delay': 5, 'MaxAttempts': 30} # Wait up to 150 seconds
        )
    
    await asyncio.to_thread(_wait)
    print("Tasks are RUNNING. Fetching IPs...")
    
    # Describe tasks to get ENIs
    response = ecs_client.describe_tasks(
        cluster=ECS_CLUSTER,
        tasks=task_arns
    )
    
    eni_ids = []
    for task in response.get("tasks", []):
        for attachment in task.get("attachments", []):
            if attachment.get("type") == "ElasticNetworkInterface":
                for detail in attachment.get("details", []):
                    if detail.get("name") == "networkInterfaceId":
                        eni_ids.append(detail.get("value"))
    
    if not eni_ids:
        return []
        
    # Get public IPs from ENIs
    eni_response = ec2_client.describe_network_interfaces(
        NetworkInterfaceIds=eni_ids
    )
    
    ips = []
    for eni in eni_response.get("NetworkInterfaces", []):
        # We need the public IP because the orchestrator is making requests over the public internet
        # Or we can use PrivateIpAddress if we are in the same VPC!
        # The Orchestrator IS in the same VPC (vpc-03e417f258d069681)
        private_ip = eni.get("PrivateIpAddress")
        if private_ip:
            ips.append(private_ip)
            
    print(f"Worker Private IPs: {ips}")
    return ips

def cleanup_workers(task_arns):
    """Stops the specified ECS tasks"""
    print(f"Cleaning up {len(task_arns)} tasks...")
    for arn in task_arns:
        try:
            ecs_client.stop_task(
                cluster=ECS_CLUSTER,
                task=arn,
                reason="Benchmark complete"
            )
        except Exception as e:
            print(f"Failed to stop task {arn}: {e}")


