targetScope = 'resourceGroup'

param location string = resourceGroup().location
param clusterName string = 'dawl-pilot'
param registryName string
param adminObjectId string
param operatorCidr string
param expiresAt string
param systemVmSize string = 'Standard_D4as_v5'
param workerVmSize string = 'Standard_D8as_v5'
@minValue(1)
@maxValue(14)
param workerNodes int = 7
param tags object = { project: 'dawl', purpose: 'four-hour-pilot', expiresAt: expiresAt }

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource cluster 'Microsoft.ContainerService/managedClusters@2024-10-01' = {
  name: clusterName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'Base', tier: 'Free' }
  properties: {
    dnsPrefix: clusterName
    enableRBAC: true
    disableLocalAccounts: true
    aadProfile: {
      managed: true
      enableAzureRBAC: true
      tenantID: subscription().tenantId
    }
    apiServerAccessProfile: { authorizedIPRanges: [operatorCidr] }
    agentPoolProfiles: [
      {
        name: 'system'
        count: 4
        vmSize: systemVmSize
        osType: 'Linux'
        osSKU: 'Ubuntu'
        osDiskSizeGB: 64
        osDiskType: 'Managed'
        mode: 'System'
        type: 'VirtualMachineScaleSets'
        maxPods: 30
        enableAutoScaling: false
        nodeLabels: { 'dawl-pool': 'system' }
      }
      {
        name: 'audit'
        count: workerNodes
        vmSize: workerVmSize
        osType: 'Linux'
        osSKU: 'Ubuntu'
        osDiskSizeGB: 64
        osDiskType: 'Managed'
        mode: 'User'
        type: 'VirtualMachineScaleSets'
        maxPods: 30
        enableAutoScaling: false
        nodeLabels: { 'dawl-pool': 'audit' }
        nodeTaints: ['dawl-audit=true:NoSchedule']
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      podCidr: '10.244.0.0/16'
      serviceCidr: '10.240.0.0/16'
      dnsServiceIP: '10.240.0.10'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
    }
    storageProfile: { diskCSIDriver: { enabled: true } }
    autoUpgradeProfile: { upgradeChannel: 'none', nodeOSUpgradeChannel: 'None' }
  }
}

resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, cluster.id, 'pull')
  scope: registry
  properties: {
    principalId: cluster.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource adminRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cluster.id, adminObjectId, 'cluster-admin')
  scope: cluster
  properties: {
    principalId: adminObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b')
  }
}

// Runs in Azure even when the operator's laptop is offline. It stops compute;
// disks, registry and network resources still require resource-group cleanup.
resource deadline 'Microsoft.Logic/workflows@2019-05-01' = {
  name: '${clusterName}-deadline'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        deadline: { type: 'Recurrence', recurrence: { frequency: 'Minute', interval: 5 } }
      }
      actions: {
        expired: {
          type: 'If'
          expression: { greaterOrEquals: ['@utcNow()', expiresAt] }
          actions: {
            state: {
              type: 'Http'
              inputs: {
                method: 'GET'
                uri: '${environment().resourceManager}${substring(cluster.id, 1)}?api-version=2024-10-01'
                authentication: { type: 'ManagedServiceIdentity' }
              }
            }
            running: {
              type: 'If'
              runAfter: { state: ['Succeeded'] }
              expression: { equals: ['@body(\'state\')?[\'properties\']?[\'powerState\']?[\'code\']', 'Running'] }
              actions: {
                stop: {
                  type: 'Http'
                  inputs: {
                    method: 'POST'
                    uri: '${environment().resourceManager}${substring(cluster.id, 1)}/stop?api-version=2024-10-01'
                    authentication: { type: 'ManagedServiceIdentity' }
                  }
                }
              }
              else: { actions: {} }
            }
          }
          else: { actions: {} }
        }
      }
      outputs: {}
    }
  }
}
resource deadlineRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cluster.id, deadline.id, 'stop')
  scope: cluster
  properties: {
    principalId: deadline.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
  }
}

output clusterId string = cluster.id
output registryHost string = registry.properties.loginServer
output deadlineWorkflow string = deadline.name
output workerCapacity int = workerNodes * 3
