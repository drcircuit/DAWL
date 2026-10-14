targetScope = 'resourceGroup'
param location string = 'norwayeast'
@minLength(3)
@maxLength(19)
param prefix string
param image string
param operatorPublicKey string
param expiresAt string
param shutdownAt string
var tags = { project: 'DAWL', pilot: prefix, expiresAt: expiresAt, cloudVcpus: '3' }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: '${prefix}acr' }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: '${prefix}state' }
resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = { name: '${prefix}-pull' }
resource pool 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: '${prefix}-pool'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${pullIdentity.id}': {} } }
  properties: {
    osType: 'Linux'
    restartPolicy: 'Never'
    sku: 'Standard'
    imageRegistryCredentials: [{ server: registry.properties.loginServer, identity: pullIdentity.id }]
    ipAddress: { type: 'Public', ports: [{ port: 2222, protocol: 'TCP' }], dnsNameLabel: '${prefix}-pool' }
    containers: [
      {
        name: 'pool'
        properties: {
          image: image
          ports: [{ port: 2222, protocol: 'TCP' }]
          resources: { requests: { cpu: 3, memoryInGB: 8 }, limits: { cpu: 3, memoryInGB: 8 } }
          environmentVariables: [
            { name: 'DAWL_SITE', value: 'cloud' }
            { name: 'DAWL_DEADLINE', value: shutdownAt }
            { name: 'DAWL_OPERATOR_PUBLIC_KEY', value: operatorPublicKey }
          ]
          volumeMounts: [{ name: 'archive', mountPath: '/persist' }]
        }
      }
    ]
    volumes: [{ name: 'archive', azureFile: {
      shareName: 'pilot-state'
      storageAccountName: storage.name
      storageAccountKey: storage.listKeys().keys[0].value
    } }]
  }
}
// Independent Azure-side backup. The container supervisor stops first and exports
// closed databases; this workflow prevents a hung process from extending compute.
resource deadline 'Microsoft.Logic/workflows@2019-05-01' = {
  name: '${prefix}-deadline'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: { deadline: { type: 'Recurrence', recurrence: { frequency: 'Minute', interval: 5 } } }
      actions: {
        expired: {
          type: 'If'
          expression: { greaterOrEquals: ['@utcNow()', expiresAt] }
          actions: {
            stop: {
              type: 'Http'
              inputs: {
                method: 'POST'
                uri: '${environment().resourceManager}${substring(pool.id,1)}/stop?api-version=2023-05-01'
                authentication: { type: 'ManagedServiceIdentity' }
              }
            }
          }
          else: { actions: {} }
        }
      }
      outputs: {}
    }
  }
}
resource stopRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(pool.id,deadline.id,'stop')
  scope: pool
  properties: {
    principalId: deadline.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions','b24988ac-6180-42a0-ab88-20f7382dd24c')
  }
}
output poolName string = pool.name
output fqdn string = pool.properties.ipAddress.fqdn
